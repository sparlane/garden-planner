"""A plant-led sale dispatches its recorded pot without charging media twice."""

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.utils import timezone

from applications.services import ApplicationRequest, LineRequest, TargetRequest, create_application_draft, post_application
from costing.services import effective_allocations
from inventory.ledger import bulk_balance, unpromised_bulk, individualize_lot_units, IndividualizationRequest, reverse_movement
from plantings.counted_fills import plant_counted_fill
from plantings.fill_numbering import number_counted_pot
from plantings.movement import move_specific_plant
from plantings.models import SpecificPlantLocation
from health.models import HealthObservationType
from locations.models import Location
from seedtrays.container_fills import open_counted_fill, open_numbered_fill
from tests.factories import make_inventory_item, make_stock_lot

from .commerce import post_fulfillment, post_return, reverse_fulfillment, reverse_return
from .models import SalesOrder, FulfillmentContainer
from .services import close_reservations
from .test_commerce import CommerceFixtureTestCase


class FillCommerceTests(CommerceFixtureTestCase):
    """Stock, media, and immutable COGS reconcile across physical corrections."""

    def setUp(self):
        super().setUp()
        self.pots = make_stock_lot(item=make_inventory_item(category='pot_container', base_unit='each', tracking_mode='mixed'),
                                   location=self.store, quantity='50', base_unit_cost=Decimal('3'))
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 50)
        self.media = make_stock_lot(location=self.store, quantity='100', base_unit_cost=Decimal('2'))
        application = create_application_draft(self.workspace, self.user, ApplicationRequest(
            timezone.now(), self.store, lines=(LineRequest(
                self.media.item, self.media, '100', 'l', usage_basis='manual', targets=(TargetRequest('container_fill', self.fill),),
            ),),
        ))
        post_application(application, self.user)
        self.plant = self.available_plant()
        plant_counted_fill(self.workspace, self.user, self.fill, [self.plant.pk])
        order, self.allocations = self.confirmed_order([self.plant])
        self.order = SalesOrder.objects.get(pk=order['pk'])

    def dispatch(self, with_pot=True):
        """Use the same explicit choice as the fulfillment form."""
        ids = [self.allocations[0]['pk']]
        return post_fulfillment(self.order, self.user, operation_key=uuid4(), allocation_ids=ids,
                                container_allocations=ids if with_pot else [])

    def test_dispatch_keeps_the_other_forty_nine_pots_and_mix_untouched(self):
        """One pot and its fixed two litres reach COGS at dispatch."""
        sale = self.dispatch()
        line, = sale.lines.all()
        self.assertEqual(line.cogs_amount, 7)
        self.assertEqual(bulk_balance(self.pots, self.store), 49)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
        self.assertEqual(FulfillmentContainer.objects.get().unit_cost, 3)
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 7)

    def test_bare_root_dispatch_keeps_the_empty_pot_and_charges_mix(self):
        """Not sending the container still consumes the plant's mix share."""
        sale = self.dispatch(with_pot=False)
        self.assertEqual(sale.lines.get().cogs_amount, 4)
        self.assertEqual(bulk_balance(self.pots, self.store), 50)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 1)
        self.assertFalse(FulfillmentContainer.objects.exists())

    def test_return_and_its_reversal_restore_exact_stock_and_container_cost(self):
        """A returned pot holds its plant again without buying mix a second time."""
        sale = self.dispatch()
        line = sale.lines.get()
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Returned.', items=[{
            'fulfillment_line': line, 'outcome': 'available', 'destination': self.store,
        }])
        self.assertEqual(bulk_balance(self.pots, self.store), 50)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 4)
        self.assertIsNotNone(returned.lines.get().container_fill_id)
        reverse_return(returned, self.user, operation_key=uuid4(), reason='Wrong return.')
        self.assertEqual(bulk_balance(self.pots, self.store), 49)
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 7)
        line.refresh_from_db()
        self.assertEqual(line.cogs_amount, 7)

    def test_reversing_dispatch_returns_the_pot_without_erasing_consumed_mix(self):
        """Correcting commerce removes only the container's sold cost layer."""
        sale = self.dispatch()
        reverse_fulfillment(sale, self.user, operation_key=uuid4(), reason='Wrong sale.')
        self.assertEqual(bulk_balance(self.pots, self.store), 50)
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 4)

    def test_numbered_counted_pot_dispatch_does_not_release_an_extra_empty_pot(self):
        """Numbering changes only the pot identity, not its mix or dispatch count."""
        placement = number_counted_pot(self.workspace, self.user, self.fill, self.plant.pk)
        sale = self.dispatch()
        self.assertEqual(sale.lines.get().container_dispatch.stock_movement.unit_id, placement.container_unit_id)
        self.assertEqual(sale.lines.get().cogs_amount, 7)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Returned.', items=[{
            'fulfillment_line': sale.lines.get(), 'outcome': 'available', 'destination': self.store,
        }])
        self.assertEqual(returned.lines.get().container_fill.inventory_unit_id, placement.container_unit_id)
        reverse_return(returned, self.user, operation_key=uuid4(), reason='Wrong return.')
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 7)

    def test_numbered_fill_closes_as_dispatched_and_can_return_in_a_new_fill(self):
        """A sale ends the numbered fill without pretending its mix was cleaned."""
        lot = make_stock_lot(item=self.pots.item, location=self.store, quantity='1', base_unit_cost=Decimal('5'))
        unit, = individualize_lot_units(self.workspace, self.user, IndividualizationRequest(lot, self.store, 1))
        fill = open_numbered_fill(self.workspace, self.user, unit)
        move_specific_plant(self.plant, {'location_type': 'container_unit', 'container_unit': unit}, self.user)
        sale = self.dispatch()
        fill.refresh_from_db()
        self.assertEqual(fill.status, 'closed')
        self.assertTrue(fill.events.filter(event_type='dispatched').exists())
        self.assertEqual(sale.lines.get().cogs_amount, 9)
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Returned.', items=[{
            'fulfillment_line': sale.lines.get(), 'outcome': 'available', 'destination': self.store,
        }])
        self.assertNotEqual(returned.lines.get().container_fill_id, fill.pk)
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch)), 4)

    def test_unknown_pot_cost_remains_unknown_in_snapshot_and_plant_layers(self):
        """Known mix cannot hide an unknown container acquisition cost."""
        type(self.pots).objects.filter(pk=self.pots.pk).update(base_unit_cost=None)
        sale = self.dispatch()
        self.assertIsNone(sale.lines.get().cogs_amount)
        layer, = [row for row in effective_allocations(self.plant.batch) if row.source_type == 'container_dispatch']
        self.assertIsNone(layer.amount)

    def test_quarantined_pot_return_remains_a_fill_at_the_quarantine_location(self):
        """Quarantine retains the physical pot without issuing any new media."""
        sale = self.dispatch()
        destination = Location.objects.create(workspace=self.workspace, name='Quarantine', code='FILL-Q', location_type='quarantine')
        observation = HealthObservationType.objects.filter(workspace=self.workspace).first()
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Inspect.', items=[{
            'fulfillment_line': sale.lines.get(), 'outcome': 'quarantined', 'destination': destination,
        }], observation_type=observation, severity='moderate')
        self.assertEqual(returned.lines.get().container_fill.source_location_id, destination.pk)
        self.assertEqual(SpecificPlantLocation.objects.get(specific_plant=self.plant, ended__isnull=True).container_fill_id,
                         returned.lines.get().container_fill_id)

    def test_return_cannot_be_reversed_after_its_pot_is_numbered(self):
        """Other empty pots cannot stand in for the returned pot's new identity."""
        sale = self.dispatch()
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Returned.', items=[{
            'fulfillment_line': sale.lines.get(), 'outcome': 'available', 'destination': self.store,
        }])
        number_counted_pot(self.workspace, self.user, returned.lines.get().container_fill, self.plant.pk)
        with self.assertRaises(ValidationError):
            reverse_return(returned, self.user, operation_key=uuid4(), reason='Wrong return.')

    def test_shared_numbered_pot_dispatch_and_return_are_one_physical_movement(self):
        """Three sold plants split five dollars once and return their single pot."""
        plants = [self.plant, self.available_plant(batch=self.plant.batch, cell_planting=self.plant.cell_planting), self.available_plant(batch=self.plant.batch, cell_planting=self.plant.cell_planting)]
        lot = make_stock_lot(item=self.pots.item, location=self.store, quantity='1', base_unit_cost=Decimal('5'))
        unit, = individualize_lot_units(self.workspace, self.user, IndividualizationRequest(lot, self.store, 1))
        open_numbered_fill(self.workspace, self.user, unit)
        for plant in plants:
            move_specific_plant(plant, {'location_type': 'container_unit', 'container_unit': unit}, self.user)
        close_reservations(self.order, self.user, [self.allocations[0]['pk']], 'release', 'Sell shared pot together.')
        order, allocations = self.confirmed_order(plants)
        combined = SalesOrder.objects.get(pk=order['pk'])
        ids = [row['pk'] for row in allocations]
        sale = post_fulfillment(combined, self.user, operation_key=uuid4(), allocation_ids=ids, container_allocations=ids)
        pots = FulfillmentContainer.objects.filter(fulfillment_line__fulfillment=sale)
        self.assertEqual(pots.values('stock_movement').distinct().count(), 1)
        self.assertEqual(sum(row.cogs_amount for row in pots), 5)
        self.assertEqual(sum(row.base_quantity for row in pots), 1)
        with self.assertRaises(ValidationError):
            post_return(combined, self.user, operation_key=uuid4(), reason='Only one.', items=[{
                'fulfillment_line': sale.lines.first(), 'outcome': 'available', 'destination': self.store,
            }])
        returned = post_return(combined, self.user, operation_key=uuid4(), reason='Returned together.', items=[{
            'fulfillment_line': row, 'outcome': 'available', 'destination': self.store,
        } for row in sale.lines.all()])
        self.assertEqual(returned.lines.exclude(return_movement=None).count(), 1)
        self.assertEqual(returned.lines.values('container_fill').distinct().count(), 1)
        reverse_return(returned, self.user, operation_key=uuid4(), reason='Wrong return.')
        self.assertEqual(sum(row.amount for row in effective_allocations(self.plant.batch) if row.source_type == 'container_dispatch'), 5)

    def test_api_retries_do_not_dispatch_or_charge_a_pot_twice(self):
        """The explicit with-pot choice is part of the operation fingerprint."""
        ids = [self.allocations[0]['pk']]
        payload = {'operation_key': str(uuid4()), 'allocation_ids': ids, 'container_allocations': ids}
        first = self.client.post(f'/sales/orders/{self.order.pk}/fulfillments/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = self.client.post(f'/sales/orders/{self.order.pk}/fulfillments/', payload, format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data['pk'], second.data['pk'])
        self.assertEqual(first.data['lines'][0]['container_dispatch']['unit_cost'], '3.000000000000')
        self.assertEqual(FulfillmentContainer.objects.count(), 1)
        payload['container_allocations'] = []
        changed = self.client.post(f'/sales/orders/{self.order.pk}/fulfillments/', payload, format='json')
        self.assertEqual(changed.status_code, 400, changed.data)

    def test_discarded_return_records_the_pot_loss_and_preserves_the_sale_snapshot(self):
        """Waste is a return followed by disposal of the same pot."""
        sale = self.dispatch()
        line = sale.lines.get()
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Broken.', items=[{
            'fulfillment_line': line, 'outcome': 'discarded',
        }])
        row = returned.lines.get()
        self.assertEqual(row.return_movement.quantity, 1)
        self.assertEqual(row.discard_movement.quantity, 1)
        self.assertEqual(bulk_balance(self.pots, self.store), 49)
        self.assertEqual(sum(layer.amount for layer in effective_allocations(self.plant.batch)), 4)
        line.refresh_from_db()
        self.assertEqual(line.cogs_amount, 7)
        reverse_return(returned, self.user, operation_key=uuid4(), reason='Wrong return.')
        self.assertEqual(bulk_balance(self.pots, self.store), 49)
        self.assertEqual(sum(layer.amount for layer in effective_allocations(self.plant.batch)), 7)

    def test_pot_movements_cannot_be_reversed_outside_their_commerce_documents(self):
        """A standalone stock correction cannot leave COGS claiming a sale."""
        sale = self.dispatch()
        with self.assertRaises(ValidationError):
            reverse_movement(sale.lines.get().container_dispatch.stock_movement, self.user, 'Wrong pot.')
        returned = post_return(self.order, self.user, operation_key=uuid4(), reason='Returned.', items=[{
            'fulfillment_line': sale.lines.get(), 'outcome': 'available', 'destination': self.store,
        }])
        with self.assertRaises(ValidationError):
            reverse_movement(returned.lines.get().return_movement, self.user, 'Wrong return.')
