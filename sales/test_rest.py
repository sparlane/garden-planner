"""REST and reservation workflow contracts for nursery sales."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.utils import timezone

from plantings.lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from tests.api import RESTContractTestCase
from tests.factories import make_seed_tray, make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .models import ReservationEvent, SalesOrder, SalesOrderAllocation


class SalesRESTTests(RESTContractTestCase):
    """Orders expose immutable terms and explicit exact-stock actions."""

    orders_url = '/sales/orders/'
    lines_url = '/sales/order-lines/'
    customers_url = '/sales/customers/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = True
        self.workspace.save()

    def create_order(self, **overrides):
        """Create one order through its public endpoint."""
        payload = {'status': SalesOrder.Status.DRAFT, 'notes': 'Counter order', **overrides}
        response = self.client.post(self.orders_url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def make_available_plant(self):
        """Create a germinated plant and record that it is ready for sale."""
        plant = make_specific_plant()
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        return plant

    def add_seedling_line(self, order, plant, quantity=1):
        """Add commercial terms matching a plant's variety."""
        response = self.client.post(self.lines_url, {
            'order': order['pk'],
            'line_type': 'seedling',
            'variety': plant.batch.variety_id,
            'description': plant.batch.variety.name,
            'quantity': quantity,
            'unit_price': '11.5000',
            'tax_rate': '15.0000',
            'discount_type': 'fixed',
            'discount_value': '3.0000',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def allocate(self, order, line, plants=None, units=None):
        """Attach exact stock through the allocation action."""
        response = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': line['pk'], 'plant_ids': plants or [], 'unit_ids': units or []},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_customer_and_order_resources_snapshot_workspace_defaults(self):
        """Walk-in and named orders share workspace-owned financial defaults."""
        customer = self.client.post(self.customers_url, {'name': 'Kauri Landscapes'}, format='json')
        self.assertEqual(customer.status_code, 201, customer.data)
        order = self.create_order(customer=customer.data['pk'])
        self.assertEqual(order['order_number'], 'SO-000001')
        self.assertEqual(order['currency_code'], 'NZD')
        self.assertTrue(order['prices_include_tax'])

    def test_inclusive_line_returns_entered_and_canonical_totals(self):
        """Inclusive entered terms extract tax and preserve reconciliation."""
        plant = self.make_available_plant()
        line = self.add_seedling_line(self.create_order(), plant, quantity=2)
        self.assertEqual(line['unit_price'], '11.5000')
        self.assertEqual(line['subtotal_ex_tax'], '17.3913')
        self.assertEqual(line['tax_total'], '2.6087')
        self.assertEqual(line['total_incl_tax'], '20.0000')

    def test_confirmation_requires_exact_allocations_and_reserves_stock(self):
        """A draft becomes confirmed only with one target per requested unit."""
        plant = self.make_available_plant()
        order = self.create_order()
        line = self.add_seedling_line(order, plant)
        incomplete = self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {}, format='json')
        self.assertEqual(incomplete.status_code, 400)
        allocations = self.allocate(order, line, plants=[plant.pk])
        confirmed = self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['status'], SalesOrder.Status.CONFIRMED)
        allocation = SalesOrderAllocation.objects.get(pk=allocations[0]['pk'])
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)
        self.assertEqual(allocation.events.get().event_type, ReservationEvent.EventType.RESERVED)

    def test_second_order_cannot_reserve_the_same_plant(self):
        """Availability is checked again while holding the plant lock."""
        plant = self.make_available_plant()
        first = self.create_order()
        first_line = self.add_seedling_line(first, plant)
        self.allocate(first, first_line, plants=[plant.pk])
        self.assertEqual(self.client.post(f"{self.orders_url}{first['pk']}/confirm/", {}).status_code, 200)

        second = self.create_order()
        second_line = self.add_seedling_line(second, plant)
        preview = self.client.post(
            f"{self.orders_url}{second['pk']}/allocation-preview/",
            {'line': second_line['pk'], 'plant_ids': [plant.pk]},
            format='json',
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data['conflicts'][0]['reason'], 'already_reserved')
        failed = self.client.post(
            f"{self.orders_url}{second['pk']}/allocate/",
            {'line': second_line['pk'], 'plant_ids': [plant.pk]},
            format='json',
        )
        self.assertEqual(failed.status_code, 400)

    def test_tentative_claims_name_competing_orders_without_blocking(self):
        """Soft claims stay selectable while every affected screen names them."""
        plant = self.make_available_plant()
        first = self.create_order()
        first_line = self.add_seedling_line(first, plant)
        self.allocate(first, first_line, plants=[plant.pk])
        second = self.create_order()
        second_line = self.add_seedling_line(second, plant)

        preview = self.client.post(
            f"{self.orders_url}{second['pk']}/allocation-preview/",
            {'line': second_line['pk'], 'plant_ids': [plant.pk]},
            format='json',
        )

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], [plant.pk])
        self.assertEqual(preview.data['conflicts'], [])
        self.assertEqual(preview.data['warnings'], [{
            'id': plant.pk,
            'reason': 'tentatively_claimed',
            'order': first['pk'],
            'order_number': first['order_number'],
            'status': SalesOrderAllocation.Status.PENDING,
        }])
        self.allocate(second, second_line, plants=[plant.pk])

        first_detail = self.client.get(f"{self.orders_url}{first['pk']}/")
        self.assertEqual(first_detail.data['lines'][0]['allocations'][0]['competing_claims'], [{
            'order': second['pk'],
            'order_number': second['order_number'],
            'status': SalesOrderAllocation.Status.PENDING,
            'quantity': None,
        }])
        register = self.client.get('/plantings/register/', {'allocation_status': 'tentative'})
        self.assertEqual(register.data['count'], 1)
        self.assertEqual(register.data['totals']['tentative'], 1)
        self.assertTrue(register.data['results'][0]['sellable'])
        self.assertEqual(
            [claim['order_number'] for claim in register.data['results'][0]['allocation_orders']],
            [first['order_number'], second['order_number']],
        )
        plant_detail = self.client.get(f'/plantings/specificplants/{plant.pk}/')
        self.assertEqual(plant_detail.data['allocation_status'], 'tentative')
        self.assertEqual(len(plant_detail.data['allocation_orders']), 2)

        confirmed = self.client.post(f"{self.orders_url}{first['pk']}/confirm/", {})
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        losing_order = self.client.get(f"{self.orders_url}{second['pk']}/")
        self.assertEqual(
            losing_order.data['lines'][0]['allocations'][0]['competing_claims'][0]['status'],
            SalesOrderAllocation.Status.RESERVED,
        )
        failed = self.client.post(f"{self.orders_url}{second['pk']}/confirm/", {})
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertIn(first['order_number'], failed.data['allocations'][0])

    def test_tentative_tray_claim_warns_and_names_the_other_order(self):
        """Serialized units use the same soft-claim contract as seedlings."""
        tray = make_seed_tray(workspace=self.workspace)
        orders = [self.create_order(), self.create_order()]
        lines = []
        for order in orders:
            response = self.client.post(self.lines_url, {
                'order': order['pk'], 'line_type': 'unit',
                'item': tray.inventory_unit.item_id,
                'description': 'Propagation tray', 'quantity': 1,
                'unit_price': '10.0000', 'tax_rate': '15.0000',
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            lines.append(response.data)
        self.allocate(orders[0], lines[0], units=[tray.inventory_unit_id])

        preview = self.client.post(
            f"{self.orders_url}{orders[1]['pk']}/allocation-preview/",
            {'line': lines[1]['pk'], 'unit_ids': [tray.inventory_unit_id]},
            format='json',
        )

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], [tray.inventory_unit_id])
        self.assertEqual(preview.data['warnings'][0]['order_number'], orders[0]['order_number'])

    def test_release_retains_history_and_restores_register_availability(self):
        """Explicit release changes availability without deleting its audit."""
        plant = self.make_available_plant()
        order = self.create_order()
        line = self.add_seedling_line(order, plant)
        allocation = self.allocate(order, line, plants=[plant.pk])[0]
        self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {})
        reserved = self.client.get('/plantings/register/', {'reserved': 'true'})
        self.assertEqual(reserved.data['count'], 1)
        self.assertEqual(reserved.data['totals']['reserved'], 1)
        released = self.client.post(
            f"{self.orders_url}{order['pk']}/release/",
            {'allocations': [allocation['pk']], 'reason': 'Customer changed quantity.'},
            format='json',
        )
        self.assertEqual(released.status_code, 200, released.data)
        available = self.client.get('/plantings/register/', {'sellable': 'true'})
        self.assertEqual(available.data['count'], 1)
        self.assertEqual(ReservationEvent.objects.filter(allocation_id=allocation['pk']).count(), 2)

    def test_tray_allocation_validates_item_and_physical_state(self):
        """Serialized tray lines reserve the exact compatible inventory unit."""
        tray = make_seed_tray(workspace=self.workspace)
        order = self.create_order(prices_include_tax=False)
        response = self.client.post(self.lines_url, {
            'order': order['pk'], 'line_type': 'unit',
            'item': tray.inventory_unit.item_id,
            'description': 'Propagation tray', 'quantity': 1,
            'unit_price': '10.0000', 'tax_rate': '15.0000',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.allocate(order, response.data, units=[tray.inventory_unit_id])
        confirmed = self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {})
        self.assertEqual(confirmed.status_code, 200, confirmed.data)

    def test_cancel_releases_reserved_stock(self):
        """Cancellation preserves reservation facts and returns stock to promise."""
        plant = self.make_available_plant()
        order = self.create_order()
        line = self.add_seedling_line(order, plant)
        allocation = self.allocate(order, line, plants=[plant.pk])[0]
        self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {})
        cancelled = self.client.post(
            f"{self.orders_url}{order['pk']}/cancel/",
            {'reason': 'Order withdrawn.'},
            format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], SalesOrder.Status.CANCELLED)
        self.assertEqual(
            SalesOrderAllocation.objects.get(pk=allocation['pk']).status,
            SalesOrderAllocation.Status.RELEASED,
        )

    def test_confirmed_terms_ignore_later_workspace_default_changes(self):
        """Changing GST defaults cannot rewrite an accepted order."""
        plant = self.make_available_plant()
        order = self.create_order()
        line = self.add_seedling_line(order, plant)
        self.allocate(order, line, plants=[plant.pk])
        confirmed = self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {})
        before = (confirmed.data['prices_include_tax'], confirmed.data['tax_total'])
        self.workspace.default_tax_rate = Decimal('12.5')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        after = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual((after.data['prices_include_tax'], after.data['tax_total']), before)

    def test_expiry_is_explicit_not_time_driven(self):
        """An overdue reservation stays active until the expire action runs."""
        plant = self.make_available_plant()
        order = self.create_order()
        line = self.add_seedling_line(order, plant)
        response = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': line['pk'], 'plant_ids': [plant.pk], 'expires_at': '2020-01-01T00:00:00Z'},
            format='json',
        )
        allocation = response.data[0]
        self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {})
        self.assertEqual(SalesOrderAllocation.objects.get(pk=allocation['pk']).status, 'reserved')
        expired = self.client.post(
            f"{self.orders_url}{order['pk']}/expire/",
            {'allocations': [allocation['pk']], 'reason': f'Expired at {timezone.now().isoformat()}.'},
            format='json',
        )
        self.assertEqual(expired.status_code, 200, expired.data)
        self.assertEqual(expired.data[0]['status'], 'expired')
