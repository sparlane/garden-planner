"""A pot's projected cost is what its plants and container record when dispatched."""
# pylint: disable=duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.test.utils import CaptureQueriesContext
from django.db import connection

from applications.services import LineRequest, TargetRequest, post_application
from inventory.ledger import IndividualizationRequest, individualize_lot_units
from plantings.counted_fills import plant_counted_fill
from plantings.models import SpecificPlant, SpecificPlantLocation
from plantings.movement import move_specific_plant
from sales.commerce import post_fulfillment
from sales.models import SalesOrder
from sales.test_commerce import CommerceFixtureTestCase
from seedtrays.container_fills import open_numbered_fill
from seedtrays.pot_media import pot_fill_cost_breakdown
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import (
    make_production_batch, make_seed_tray_cell_planting, make_seed_tray_planting,
    make_specific_plant_location, make_stock_lot, quarantine_stock,
)

from .pot_pending import pot_fill_pending_cost
from .services import plant_cost_breakdown, reallocate_batch


class PotPendingCostTests(PotMediaMixin, CommerceFixtureTestCase):  # pylint: disable=too-many-ancestors
    """Price numbered and counted pots through the real dispatch path."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        type(self.pots).objects.filter(pk=self.pots.pk).update(base_unit_cost=Decimal('1.25'))
        self.pots.refresh_from_db()

    def plants(self, count):
        """Saleable plants of one variety, each raised in a different batch."""
        first = self.available_plant()
        variety = first.batch.variety
        return [first] + [self.available_plant(cell_planting=make_seed_tray_cell_planting(
            seed_tray_planting=make_seed_tray_planting(batch=make_production_batch(variety=variety)),
        )) for _ in range(count - 1)]

    def unit(self, cost='5'):
        """Number one pot at a stated acquisition cost."""
        unit, = individualize_lot_units(self.workspace, self.user, IndividualizationRequest(self.pots, self.store, 1))
        type(unit).objects.filter(pk=unit.pk).update(acquisition_cost=None if cost is None else Decimal(cost))
        unit.refresh_from_db()
        return unit

    def numbered(self, plants, unit=None, media=None):
        """One numbered pot of fifty litres holding every given plant."""
        unit = unit or self.unit()
        fill = open_numbered_fill(self.workspace, self.user, unit)
        line = LineRequest(item=self.media_item, lot=media or self.media, applied_quantity='50',
                           unit_code='l', usage_basis='manual', targets=(TargetRequest('container_fill', fill),))
        post_application(self.draft(fill, lines=(line,)), self.user)
        for plant in plants:
            make_specific_plant_location(specific_plant=plant, location_type='container_unit',
                                         container_unit=unit, seed_tray_cell=None)
        return fill

    def potted_on(self, plant, lot):
        """Give a plant a committed media layer from lot, then stand it in a new pot."""
        self.numbered([plant], media=lot)
        unit = self.unit()
        fill = self.numbered([], unit=unit)
        move_specific_plant(plant, {'location_type': SpecificPlantLocation.CONTAINER_UNIT, 'container_unit': unit}, self.user)
        reallocate_batch(plant.batch, self.user, 'manual_recalculate')
        return fill

    def dispatch(self, plants):
        """Sell every plant with its pot and return the recorded cost of sale."""
        order, allocations = self.confirmed_order(plants)
        ids = [row['pk'] for row in allocations]
        sale = post_fulfillment(SalesOrder.objects.get(pk=order['pk']), self.user,
                                operation_key=uuid4(), allocation_ids=ids, container_allocations=ids)
        return sum(line.cogs_amount for line in sale.lines.all())

    def test_numbered_pot_of_three_batches_equals_its_dispatch(self):
        """One pot, three plants, one container, and the sum the sale records."""
        plants = self.plants(3)
        fill = self.numbered(plants)
        report = pot_fill_pending_cost(fill)
        pot, = report['pots']
        self.assertEqual(report['pot_count'], 1)
        self.assertEqual(len({row['batch'] for row in pot['plants']}), 3)
        self.assertEqual(pot['container_unit'], fill.inventory_unit_id)
        self.assertEqual(pot['container_cost']['amount'], '5.0000')
        self.assertEqual(sum(Decimal(row['pending'][1]['amount']) for row in pot['plants']), Decimal('5'))
        self.assertEqual(sum(Decimal(row['pending'][0]['amount']) for row in pot['plants']), Decimal('100'))
        self.assertEqual(pot['total'], '105.0000')
        self.assertEqual(report['total'], pot['total'])
        self.assertFalse(pot['mixed_currency'])
        self.assertTrue(pot['dispatchable'])
        self.assertEqual(self.dispatch(plants), Decimal(pot['total']))

    def test_counted_bench_reports_planted_pots_only(self):
        """Three of fifty pots planted: three containers and three shares of media."""
        plants = self.plants(3)
        post_application(self.draft(), self.user)
        plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk for plant in plants])
        report = pot_fill_pending_cost(self.fill)
        self.assertEqual(report['pot_count'], 3)
        self.assertEqual([row['container_cost']['amount'] for row in report['pots']], ['1.2500'] * 3)
        for pot in report['pots']:
            row, = pot['plants']
            self.assertIsNone(pot['container_unit'])
            self.assertEqual(pot['total'], plant_cost_breakdown(SpecificPlant.objects.get(pk=row['plant']))['sale_with_pot'])
        self.assertEqual(report['total'], '9.7500')
        media = sum(Decimal(pot['plants'][0]['pending'][0]['amount']) for pot in report['pots'])
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'] - media, Decimal('94'))
        self.assertEqual(self.dispatch(plants), Decimal(report['total']))

    def test_committed_layers_are_included(self):
        """A plant's earlier media is part of what its pot has cost."""
        plant, = self.plants(1)
        fill = self.potted_on(plant, self.media)
        pot, = pot_fill_pending_cost(fill)['pots']
        self.assertEqual(pot['plants'][0]['committed']['total'], '100.0000')
        self.assertEqual(pot['total'], '205.0000')
        self.assertEqual(self.dispatch([plant]), Decimal(pot['total']))

    def test_unpriced_pot_makes_the_total_unknown(self):
        """No container cost cannot be read as a free container."""
        plants = self.plants(1)
        report = pot_fill_pending_cost(self.numbered(plants, unit=self.unit(cost=None)))
        pot, = report['pots']
        self.assertTrue(pot['container_cost']['unknown_cost'])
        self.assertTrue(pot['unknown_cost'])
        self.assertIsNone(pot['total'])
        self.assertIsNone(report['total'])

    def test_unpriced_media_makes_the_total_unknown(self):
        """An unpriced mix cannot produce a deceptively small pot."""
        plants = self.plants(2)
        fill = self.numbered(plants)
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        report = pot_fill_pending_cost(fill)
        self.assertTrue(report['unknown_cost'])
        self.assertIsNone(report['pots'][0]['total'])
        self.assertIsNone(report['total'])

    def test_unknown_committed_layer_makes_the_total_unknown(self):
        """An unknown layer on one occupant leaves the whole pot unknown."""
        plant, = self.plants(1)
        unpriced = make_stock_lot(item=self.media_item, location=self.store, quantity='100', base_unit_cost=None)
        report = pot_fill_pending_cost(self.potted_on(plant, unpriced))
        pot, = report['pots']
        self.assertTrue(pot['plants'][0]['committed']['unknown_cost'])
        self.assertIsNone(pot['total'])
        self.assertIsNone(report['total'])

    def test_two_currencies_are_reported_and_not_combined(self):
        """A plant raised on imported mix keeps that cost in its own currency."""
        plant, = self.plants(1)
        imported = make_stock_lot(item=self.media_item, location=self.store, quantity='100',
                                  base_unit_cost=Decimal('3'), currency_code='USD')
        report = pot_fill_pending_cost(self.potted_on(plant, imported))
        pot, = report['pots']
        home = self.workspace.currency_code
        self.assertEqual(pot['totals'], sorted([
            {'currency_code': 'USD', 'amount': '150.0000'},
            {'currency_code': home, 'amount': '105.0000'},
        ], key=lambda row: row['currency_code']))
        self.assertTrue(pot['mixed_currency'])
        self.assertIsNone(pot['total'])
        self.assertIsNone(report['total'])
        self.assertTrue(report['mixed_currency'])

    def test_quarantined_occupant_is_costed_and_flagged(self):
        """Cost is not sellability: report it, and say which plant cannot leave."""
        plants = self.plants(2)
        fill = self.numbered(plants)
        quarantine_stock(self.workspace, self.user, [{'type': 'plant', 'id': plants[1].pk}])
        pot, = pot_fill_pending_cost(fill)['pots']
        self.assertEqual(pot['total'], '105.0000')
        self.assertFalse(pot['dispatchable'])
        self.assertEqual({row['plant']: row['dispatch_blocked'] for row in pot['plants']},
                         {plants[0].pk: None, plants[1].pk: 'quarantined'})

    def test_contents_keeps_costs_and_digest(self):
        """The preview adds pot costs without changing its existing readers' fields."""
        plants = self.plants(3)
        post_application(self.draft(), self.user)
        plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk for plant in plants[:1]])
        url = f'/seedtrays/container-fills/{self.fill.pk}/contents/'
        before = self.client.get(url)
        self.assertEqual(before.status_code, 200, before.data)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertFalse(any(query['sql'].lstrip().split()[0] in ('INSERT', 'UPDATE', 'DELETE') for query in queries))
        self.assertEqual(response.data['digest'], before.data['digest'])
        self.assertEqual(response.data['costs'], {key: format(value, 'f') if isinstance(value, Decimal) else value
                                                  for key, value in pot_fill_cost_breakdown(self.fill).items()})
        self.assertEqual(response.data['pot_costs']['pot_count'], 1)
        self.assertEqual(response.data['pot_costs']['total'], '3.2500')
