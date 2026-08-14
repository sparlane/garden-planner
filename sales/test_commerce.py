"""End-to-end contracts for fulfillment, cash, returns, and refunds."""

# pylint: disable=duplicate-code,missing-function-docstring

from decimal import Decimal
from uuid import uuid4

from health.models import HealthObservationType, QuarantineCase
from inventory.models import InventoryItem, StockMovement
from locations.models import Location
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
)
from tests.api import RESTContractTestCase
from tests.factories import make_inventory_item, make_specific_plant, make_stock_lot
from workspaces.models import Workspace, get_current_workspace

from .models import Fulfillment, Payment, Refund, SalesOrder, SalesOrderAllocation


class CommerceRESTTests(RESTContractTestCase):
    """Posted commerce stays exact, linked, idempotent, and reversible."""

    orders_url = '/sales/orders/'
    lines_url = '/sales/order-lines/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.store = Location.objects.create(
            workspace=self.workspace, name='Dispatch store', code='DISPATCH',
            location_type=Location.LocationType.STORAGE,
        )

    def available_plant(self, batch=None, cell_planting=None):
        values = {'workspace': self.workspace}
        if batch is not None:
            values['batch'] = batch
        if cell_planting is not None:
            values['cell_planting'] = cell_planting
        plant = make_specific_plant(**values)
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        return plant

    def confirmed_order(self, plants):
        response = self.client.post(self.orders_url, {'status': 'draft'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        order = response.data
        response = self.client.post(self.lines_url, {
            'order': order['pk'], 'line_type': 'seedling',
            'variety': plants[0].batch.variety_id,
            'description': 'Sale seedlings', 'quantity': len(plants),
            'unit_price': '10.0000', 'tax_rate': '15.0000',
            'discount_type': 'fixed', 'discount_value': '1.0000',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        allocated = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': response.data['pk'], 'plant_ids': [plant.pk for plant in plants]},
            format='json',
        )
        self.assertEqual(allocated.status_code, 201, allocated.data)
        confirmed = self.client.post(
            f"{self.orders_url}{order['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        return confirmed.data, allocated.data

    def fulfill(self, order, allocations):
        response = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/",
            {'operation_key': str(uuid4()), 'allocation_ids': allocations},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_partial_then_complete_fulfillment_reconciles_exact_positions(self):
        first_plant = self.available_plant()
        plants = [first_plant, self.available_plant(
            batch=first_plant.batch, cell_planting=first_plant.cell_planting,
        )]
        order, allocations = self.confirmed_order(plants)
        first = self.fulfill(order, [allocations[0]['pk']])
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual(detail.data['status'], SalesOrder.Status.PARTIALLY_FULFILLED)
        self.assertEqual(detail.data['commerce']['fulfilled_quantity'], 1)
        second = self.fulfill(order, [allocations[1]['pk']])
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual(detail.data['status'], SalesOrder.Status.FULFILLED)
        fulfilled_total = Decimal(first['lines'][0]['total_incl_tax'])
        fulfilled_total += Decimal(second['lines'][0]['total_incl_tax'])
        self.assertEqual(fulfilled_total, Decimal('21.8500'))
        self.assertTrue(all(
            plant_lifecycle_summary(plant).state == LifecycleState.SOLD
            for plant in plants
        ))

    def test_retry_returns_the_original_fulfillment_without_double_sale(self):
        order, allocations = self.confirmed_order([self.available_plant()])
        key = str(uuid4())
        payload = {'operation_key': key, 'allocation_ids': [allocations[0]['pk']]}
        first = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/", payload, format='json',
        )
        second = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/", payload, format='json',
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['pk'], first.data['pk'])
        self.assertEqual(Fulfillment.objects.filter(reversal_of__isnull=True).count(), 1)

    def test_packaging_consumption_is_costed_and_linked(self):
        order, allocations = self.confirmed_order([self.available_plant()])
        item = make_inventory_item(
            workspace=self.workspace, category=InventoryItem.Category.PACKAGING,
        )
        lot = make_stock_lot(
            workspace=self.workspace, item=item, location=self.store,
            quantity=Decimal('10'), base_unit_cost=Decimal('2'),
            acquisition_total=Decimal('20'),
        )
        response = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/", {
                'operation_key': str(uuid4()),
                'allocation_ids': [allocations[0]['pk']],
                'packaging': [{
                    'lot': lot.pk, 'source': self.store.pk, 'quantity': '2',
                }],
            }, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['packaging_lines'][0]['cogs_amount'], '4.0000')
        self.assertEqual(StockMovement.objects.filter(
            movement_type=StockMovement.MovementType.CONSUMPTION,
            reference=f"fulfillment:{response.data['pk']}",
        ).count(), 1)

    def test_payment_refund_and_reversals_preserve_balances(self):
        order, allocations = self.confirmed_order([self.available_plant()])
        fulfillment = self.fulfill(order, [allocations[0]['pk']])
        payment = self.client.post(f"{self.orders_url}{order['pk']}/payments/", {
            'operation_key': str(uuid4()), 'paid_on': '2026-08-14',
            'amount': '10.3500', 'method': 'card',
        }, format='json')
        self.assertEqual(payment.status_code, 201, payment.data)
        refund = self.client.post(f"{self.orders_url}{order['pk']}/refunds/", {
            'operation_key': str(uuid4()), 'payment': payment.data['pk'],
            'fulfillment_lines': [fulfillment['lines'][0]['pk']],
            'amount': '5.1750', 'reason': 'Goodwill adjustment',
        }, format='json')
        self.assertEqual(refund.status_code, 201, refund.data)
        self.assertEqual(refund.data['lines'][0]['tax_total'], '0.6750')
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual(detail.data['commerce']['payment_status'], 'paid')
        reversed_refund = self.client.post(
            f"{self.orders_url}{order['pk']}/refunds/{refund.data['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Refund entered twice.'},
            format='json',
        )
        self.assertEqual(reversed_refund.status_code, 201, reversed_refund.data)
        reversed_payment = self.client.post(
            f"{self.orders_url}{order['pk']}/payments/{payment.data['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Card payment voided.'},
            format='json',
        )
        self.assertEqual(reversed_payment.status_code, 201, reversed_payment.data)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(Refund.objects.count(), 2)

    def test_available_return_reopens_quantity_for_a_new_allocation(self):
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        fulfillment = self.fulfill(order, [allocations[0]['pk']])
        returned = self.client.post(f"{self.orders_url}{order['pk']}/returns/", {
            'operation_key': str(uuid4()), 'reason': 'Customer changed plans.',
            'items': [{
                'fulfillment_line': fulfillment['lines'][0]['pk'],
                'outcome': 'available', 'destination': self.store.pk,
            }],
        }, format='json')
        self.assertEqual(returned.status_code, 201, returned.data)
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.AVAILABLE)
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual(detail.data['status'], SalesOrder.Status.CONFIRMED)
        replacement = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': fulfillment['lines'][0]['allocation'] and order['lines'][0]['pk'],
             'plant_ids': [plant.pk]},
            format='json',
        )
        self.assertEqual(replacement.status_code, 201, replacement.data)
        self.assertNotEqual(replacement.data[0]['pk'], allocations[0]['pk'])
        self.assertEqual(replacement.data[0]['status'], SalesOrderAllocation.Status.RESERVED)

    def test_fulfillment_and_return_reversals_restore_prior_states(self):
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        fulfillment = self.fulfill(order, [allocations[0]['pk']])
        returned = self.client.post(f"{self.orders_url}{order['pk']}/returns/", {
            'operation_key': str(uuid4()), 'reason': 'Temporary return.',
            'items': [{
                'fulfillment_line': fulfillment['lines'][0]['pk'],
                'outcome': 'available', 'destination': self.store.pk,
            }],
        }, format='json')
        reversed_return = self.client.post(
            f"{self.orders_url}{order['pk']}/returns/{returned.data['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Return was entered in error.'},
            format='json',
        )
        self.assertEqual(reversed_return.status_code, 201, reversed_return.data)
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.SOLD)
        reversed_fulfillment = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/{fulfillment['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Dispatch was entered in error.'},
            format='json',
        )
        self.assertEqual(
            reversed_fulfillment.status_code, 201, reversed_fulfillment.data,
        )
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.AVAILABLE)
        allocation = SalesOrderAllocation.objects.get(pk=allocations[0]['pk'])
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)

    def test_quarantined_return_opens_a_formal_health_case(self):
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        fulfillment = self.fulfill(order, [allocations[0]['pk']])
        quarantine = Location.objects.create(
            workspace=self.workspace, name='Return quarantine', code='RETURN-Q',
            location_type=Location.LocationType.QUARANTINE,
        )
        observation_type = HealthObservationType.objects.get(
            workspace=self.workspace, code='physical-damage',
        )
        response = self.client.post(f"{self.orders_url}{order['pk']}/returns/", {
            'operation_key': str(uuid4()), 'reason': 'Damage found on return.',
            'observation_type': observation_type.pk, 'severity': 'moderate',
            'items': [{
                'fulfillment_line': fulfillment['lines'][0]['pk'],
                'outcome': 'quarantined', 'destination': quarantine.pk,
            }],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNotNone(response.data['health_observation'])
        self.assertTrue(QuarantineCase.objects.filter(pk=response.data['quarantine_case']).exists())
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.QUARANTINED)
