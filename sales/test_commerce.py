"""End-to-end contracts for fulfillment, cash, returns, and refunds."""

# pylint: disable=duplicate-code,missing-function-docstring

from decimal import Decimal
from uuid import uuid4

from health.availability import is_quarantined
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

from .models import (
    Fulfillment,
    Payment,
    Refund,
    SalesOrder,
    SalesOrderAllocation,
    SalesReturn,
)


class CommerceFixtureTestCase(RESTContractTestCase):
    """A nursery workspace with the helpers every commerce flow needs."""

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


class CommerceRESTTests(CommerceFixtureTestCase):
    """Posted commerce stays exact, linked, idempotent, and reversible."""

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

    def quarantined_return(self, plant):
        """Sell one plant and take it back into quarantine as damaged."""
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
        return order, response.data

    def act_on_case(self, sales_return, action_name, reason):
        """Close the case a quarantined return opened, as an operator would."""
        response = self.client.post(
            f"/health/quarantines/{sales_return['quarantine_case']}/{action_name}/",
            {'idempotency_key': str(uuid4()), 'reason': reason},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['active'])
        return response.data

    def test_quarantined_return_opens_a_formal_health_case(self):
        plant = self.available_plant()
        _order, sales_return = self.quarantined_return(plant)
        self.assertIsNotNone(sales_return['health_observation'])
        self.assertTrue(
            QuarantineCase.objects.filter(pk=sales_return['quarantine_case']).exists()
        )
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.QUARANTINED)

    def test_a_released_return_becomes_saleable_stock_again(self):
        plant = self.available_plant()
        _order, sales_return = self.quarantined_return(plant)
        self.act_on_case(sales_return, 'release', 'Recovered in isolation.')
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.AVAILABLE)
        self.assertTrue(summary.sellable)
        resold, allocations = self.confirmed_order([plant])
        self.fulfill(resold, [allocations[0]['pk']])
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.SOLD)

    def test_a_culled_return_is_resolved_without_denying_the_quarantine(self):
        plant = self.available_plant()
        _order, sales_return = self.quarantined_return(plant)
        self.act_on_case(sales_return, 'cull', 'Disease confirmed on assessment.')
        summary = plant_lifecycle_summary(plant)
        self.assertEqual(summary.state, LifecycleState.CULLED)
        self.assertEqual(summary.final_outcome, EventType.CULLED)
        self.assertEqual(
            list(
                plant.lifecycle_events.order_by('occurred_at', 'pk')
                .values_list('event_type', flat=True)
            ),
            [
                EventType.GERMINATED,
                EventType.READY,
                EventType.SOLD,
                EventType.RETURNED_QUARANTINED,
                EventType.CULLED,
            ],
        )

    def test_reversing_a_quarantined_return_restores_the_sale(self):
        plant = self.available_plant()
        order, sales_return = self.quarantined_return(plant)
        reversed_return = self.client.post(
            f"{self.orders_url}{order['pk']}/returns/{sales_return['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Return was entered in error.'},
            format='json',
        )
        self.assertEqual(reversed_return.status_code, 201, reversed_return.data)
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.SOLD)
        self.assertFalse(
            plant.lifecycle_events
            .filter(event_type=EventType.RELEASED_AVAILABLE)
            .exists()
        )
        self.assertFalse(is_quarantined(plant))


class OrderStatusMachineTests(CommerceFixtureTestCase):
    """Every status an order reaches has a way out of it.

    The order status machine is enforced by guards spread across services and
    commerce rather than declared in one table, so the table below is the
    test's own and `test_the_declared_transitions_are_the_real_ones` is what
    keeps it honest. Task 91's lifecycle invariant is the model: assert that
    no reachable unresolved status is a dead end, rather than assert one path.
    """

    # status -> action -> the status the action leaves behind, or None when the
    # action is refused from there.
    TRANSITIONS = {
        SalesOrder.Status.QUOTE: {
            'to_draft': SalesOrder.Status.DRAFT,
            'confirm': None,
            'fulfill': None,
            'return_item': None,
            'cancel': SalesOrder.Status.CANCELLED,
        },
        SalesOrder.Status.DRAFT: {
            'to_draft': None,
            'confirm': SalesOrder.Status.CONFIRMED,
            'fulfill': None,
            'return_item': None,
            'cancel': SalesOrder.Status.CANCELLED,
        },
        SalesOrder.Status.CONFIRMED: {
            'to_draft': None,
            'confirm': None,
            'fulfill': SalesOrder.Status.PARTIALLY_FULFILLED,
            'return_item': None,
            'cancel': SalesOrder.Status.CANCELLED,
        },
        SalesOrder.Status.PARTIALLY_FULFILLED: {
            'to_draft': None,
            'confirm': None,
            'fulfill': SalesOrder.Status.FULFILLED,
            'return_item': SalesOrder.Status.CONFIRMED,
            # Cancelling the undispatched remainder is a real operation, and
            # the revenue already recognized on the dispatched part stays.
            'cancel': SalesOrder.Status.CANCELLED,
        },
        SalesOrder.Status.FULFILLED: {
            'to_draft': None,
            'confirm': None,
            'fulfill': None,
            'return_item': SalesOrder.Status.PARTIALLY_FULFILLED,
            'cancel': None,
        },
        SalesOrder.Status.CANCELLED: {
            'to_draft': None,
            'confirm': None,
            'fulfill': None,
            'return_item': None,
            'cancel': None,
        },
    }

    # A cancelled order is resolved: it holds no unfulfilled commitment and
    # nothing is waiting on it. Every other status still owes someone an act.
    RESOLVED = {SalesOrder.Status.CANCELLED}

    def staged_order(self, target):
        """Return an order standing in one status, with its allocations."""
        first = self.available_plant()
        plants = [first, self.available_plant(
            batch=first.batch, cell_planting=first.cell_planting,
        )]
        entry = 'quote' if target == SalesOrder.Status.QUOTE else 'draft'
        created = self.client.post(self.orders_url, {'status': entry}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        order = created.data
        line = self.client.post(self.lines_url, {
            'order': order['pk'], 'line_type': 'seedling',
            'variety': first.batch.variety_id,
            'description': 'Sale seedlings', 'quantity': len(plants),
            'unit_price': '10.0000', 'tax_rate': '15.0000',
            'discount_type': 'fixed', 'discount_value': '1.0000',
        }, format='json')
        self.assertEqual(line.status_code, 201, line.data)
        allocated = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': line.data['pk'], 'plant_ids': [plant.pk for plant in plants]},
            format='json',
        )
        self.assertEqual(allocated.status_code, 201, allocated.data)
        allocations = [row['pk'] for row in allocated.data]
        if target in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
            return order, allocations
        if target == SalesOrder.Status.CANCELLED:
            self.act(order, 'cancel')
            return order, allocations
        confirmed = self.client.post(
            f"{self.orders_url}{order['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        if target == SalesOrder.Status.PARTIALLY_FULFILLED:
            self.fulfill(order, allocations[:1])
        elif target == SalesOrder.Status.FULFILLED:
            self.fulfill(order, allocations)
        return order, allocations

    def reserved_allocations(self, order):
        """Return the allocations still standing ready to dispatch."""
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        return [
            allocation['pk']
            for line in detail.data['lines']
            for allocation in line['allocations']
            if allocation['status'] == SalesOrderAllocation.Status.RESERVED
        ]

    def returnable_line(self, order):
        """Return one dispatched item that no return has already taken back."""
        listed = self.client.get(f"{self.orders_url}{order['pk']}/fulfillments/")
        returns = self.client.get(f"{self.orders_url}{order['pk']}/returns/")
        taken = {
            item['fulfillment_line']
            for sales_return in returns.data
            if sales_return['status'] == 'posted'
            for item in sales_return['lines']
        }
        for fulfillment in listed.data:
            if fulfillment['status'] != 'posted':
                continue
            for row in fulfillment['lines']:
                if row['pk'] not in taken:
                    return row['pk']
        return None

    def act(self, order, action):
        """Attempt one operator action and return its HTTP response."""
        base = f"{self.orders_url}{order['pk']}/"
        if action == 'to_draft':
            return self.client.post(f'{base}to-draft/', {}, format='json')
        if action == 'confirm':
            return self.client.post(f'{base}confirm/', {}, format='json')
        if action == 'cancel':
            return self.client.post(
                f'{base}cancel/', {'reason': 'Customer withdrew.'}, format='json',
            )
        if action == 'fulfill':
            return self.client.post(f'{base}fulfillments/', {
                'operation_key': str(uuid4()),
                'allocation_ids': self.reserved_allocations(order)[:1],
            }, format='json')
        if action == 'return_item':
            line = self.returnable_line(order)
            return self.client.post(f'{base}returns/', {
                'operation_key': str(uuid4()), 'reason': 'Customer changed plans.',
                'items': [] if line is None else [{
                    'fulfillment_line': line, 'outcome': 'available',
                    'destination': self.store.pk,
                }],
            }, format='json')
        raise ValueError(f'Unknown order action: {action!r}')

    def observe(self, source, action):
        """Attempt one action from one status and report where it lands."""
        order, _allocations = self.staged_order(source)
        before = self.client.get(f"{self.orders_url}{order['pk']}/").data['status']
        self.assertEqual(before, source)
        response = self.act(order, action)
        after = self.client.get(f"{self.orders_url}{order['pk']}/").data['status']
        if response.status_code >= 400:
            self.assertEqual(after, source, response.data)
            return None
        return after

    def test_the_declared_transitions_are_the_real_ones(self):
        """Each action is attempted from each status against the real API."""
        observed = {
            source: {
                action: self.observe(source, action)
                for action in sorted(actions)
            }
            for source, actions in self.TRANSITIONS.items()
        }
        self.assertEqual(observed, {
            source: dict(sorted(actions.items()))
            for source, actions in self.TRANSITIONS.items()
        })

    def test_every_status_the_machine_declares_is_a_real_choice(self):
        """The table cannot describe a status the model does not have."""
        named = set(self.TRANSITIONS) | {
            target for actions in self.TRANSITIONS.values()
            for target in actions.values() if target is not None
        }
        self.assertEqual(named, set(SalesOrder.Status.values))

    def test_no_unresolved_status_is_a_dead_end(self):
        """An order is never stuck somewhere with nothing left to do."""
        self.assertEqual(self.statuses_without_exits(self.TRANSITIONS), set())

    def test_a_status_added_without_an_exit_is_reported(self):
        """The invariant catches the omission rather than assuming care."""
        stuck = dict(self.TRANSITIONS)
        stuck[SalesOrder.Status.FULFILLED] = {
            action: None for action in stuck[SalesOrder.Status.FULFILLED]
        }
        self.assertEqual(
            self.statuses_without_exits(stuck), {SalesOrder.Status.FULFILLED},
        )

    def statuses_without_exits(self, transitions):
        """Return reachable unresolved statuses that admit no next action."""
        reachable = {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT} | {
            target for actions in transitions.values()
            for target in actions.values() if target is not None
        }
        return {
            source for source in reachable
            if source not in self.RESOLVED and not any(
                target is not None and target != source
                for target in transitions[source].values()
            )
        }


class RefundBoundaryTests(CommerceFixtureTestCase):
    """Refunds stop at the balances that fund them and split exactly."""

    def paid_order(self, count=1):
        """Dispatch some seedlings and take the money for them."""
        first = self.available_plant()
        plants = [first] + [
            self.available_plant(batch=first.batch, cell_planting=first.cell_planting)
            for _ in range(count - 1)
        ]
        order, allocations = self.confirmed_order(plants)
        fulfillment = self.fulfill(order, [row['pk'] for row in allocations])
        payment = self.client.post(f"{self.orders_url}{order['pk']}/payments/", {
            'operation_key': str(uuid4()), 'paid_on': '2026-08-14',
            'amount': order['total_incl_tax'], 'method': 'card',
        }, format='json')
        self.assertEqual(payment.status_code, 201, payment.data)
        return order, payment.data, fulfillment

    def refund(self, order, payment, lines, amount, **overrides):
        """Post one refund against named dispatched items."""
        payload = {
            'operation_key': str(uuid4()), 'payment': payment['pk'],
            'fulfillment_lines': [row['pk'] for row in lines],
            'amount': amount, 'reason': 'Goodwill adjustment',
        }
        payload.update(overrides)
        return self.client.post(
            f"{self.orders_url}{order['pk']}/refunds/", payload, format='json',
        )

    def test_a_refund_beyond_the_payment_balance_is_refused(self):
        order, payment, fulfillment = self.paid_order()
        response = self.refund(
            order, payment, fulfillment['lines'], '11.0000',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('amount', response.data)
        self.assertEqual(Refund.objects.count(), 0)

    def test_a_second_refund_cannot_exceed_what_the_first_left(self):
        order, payment, fulfillment = self.paid_order()
        first = self.refund(order, payment, fulfillment['lines'], '9.0000')
        self.assertEqual(first.status_code, 201, first.data)
        second = self.refund(order, payment, fulfillment['lines'], '2.0000')
        self.assertEqual(second.status_code, 400, second.data)
        self.assertIn('amount', second.data)
        remainder = self.refund(order, payment, fulfillment['lines'], '1.3500')
        self.assertEqual(remainder.status_code, 201, remainder.data)
        detail = self.client.get(f"{self.orders_url}{order['pk']}/")
        self.assertEqual(
            detail.data['commerce']['refunded_total_incl_tax'], '10.3500',
        )
        self.assertEqual(detail.data['commerce']['net_paid_total'], '0.0000')

    def test_a_refund_splits_across_lines_and_adds_back_to_the_amount(self):
        order, payment, fulfillment = self.paid_order(count=3)
        response = self.refund(order, payment, fulfillment['lines'], '10.0000')
        self.assertEqual(response.status_code, 201, response.data)
        lines = response.data['lines']
        self.assertEqual(len(lines), 3)
        self.assertEqual(
            sum(Decimal(row['total_incl_tax']) for row in lines), Decimal('10.0000'),
        )
        for row in lines:
            self.assertEqual(
                Decimal(row['subtotal_ex_tax']) + Decimal(row['tax_total']),
                Decimal(row['total_incl_tax']),
            )

    def test_one_operation_key_means_one_refund(self):
        order, payment, fulfillment = self.paid_order()
        key = str(uuid4())
        first = self.refund(
            order, payment, fulfillment['lines'], '5.1750', operation_key=key,
        )
        second = self.refund(
            order, payment, fulfillment['lines'], '5.1750', operation_key=key,
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['pk'], first.data['pk'])
        self.assertEqual(Refund.objects.count(), 1)

    def test_one_operation_key_cannot_mean_two_different_refunds(self):
        order, payment, fulfillment = self.paid_order()
        key = str(uuid4())
        self.refund(
            order, payment, fulfillment['lines'], '5.1750', operation_key=key,
        )
        conflict = self.refund(
            order, payment, fulfillment['lines'], '1.0000', operation_key=key,
        )
        self.assertEqual(conflict.status_code, 400, conflict.data)
        self.assertIn('operation_key', conflict.data)
        self.assertEqual(Refund.objects.count(), 1)

    def test_a_reversed_payment_can_no_longer_fund_a_refund(self):
        order, payment, fulfillment = self.paid_order()
        reversed_payment = self.client.post(
            f"{self.orders_url}{order['pk']}/payments/{payment['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Card payment voided.'},
            format='json',
        )
        self.assertEqual(reversed_payment.status_code, 201, reversed_payment.data)
        response = self.refund(order, payment, fulfillment['lines'], '1.0000')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('payment', response.data)


class ReversalBoundaryTests(CommerceFixtureTestCase):
    """A reversal is appended once, and nothing reverses a reversal."""

    def setUp(self):
        super().setUp()
        order, allocations = self.confirmed_order([self.available_plant()])
        self.order = order
        self.fulfillment = self.fulfill(order, [allocations[0]['pk']])
        payment = self.client.post(f"{self.orders_url}{order['pk']}/payments/", {
            'operation_key': str(uuid4()), 'paid_on': '2026-08-14',
            'amount': '10.3500', 'method': 'card',
        }, format='json')
        self.assertEqual(payment.status_code, 201, payment.data)
        self.payment = payment.data
        refund = self.client.post(f"{self.orders_url}{order['pk']}/refunds/", {
            'operation_key': str(uuid4()), 'payment': payment.data['pk'],
            'fulfillment_lines': [self.fulfillment['lines'][0]['pk']],
            'amount': '5.1750', 'reason': 'Goodwill adjustment',
        }, format='json')
        self.assertEqual(refund.status_code, 201, refund.data)
        self.refund = refund.data

    def reverse(self, collection, document_id, reason='Entered in error.'):
        """Ask for one document in this order to be reversed."""
        return self.client.post(
            f"{self.orders_url}{self.order['pk']}/{collection}/{document_id}/reverse/",
            {'operation_key': str(uuid4()), 'reason': reason},
            format='json',
        )

    def test_a_reversal_cannot_itself_be_reversed(self):
        """The route finds a reversal, so the guard has to name it.

        Locking the original with `reversal_of__isnull=True` used to filter the
        reversal out and raise `DoesNotExist`, which reached the client as a
        server error rather than a field error.
        """
        reversal = self.reverse('refunds', self.refund['pk'])
        self.assertEqual(reversal.status_code, 201, reversal.data)
        again = self.reverse('refunds', reversal.data['pk'])
        self.assertEqual(again.status_code, 400, again.data)
        self.assertIn('refund', again.data)
        self.assertEqual(Refund.objects.count(), 2)

    def test_no_document_type_lets_a_reversal_be_reversed(self):
        """Every reversible document refuses the same way."""
        sales_return = self.client.post(
            f"{self.orders_url}{self.order['pk']}/returns/", {
                'operation_key': str(uuid4()), 'reason': 'Customer changed plans.',
                'items': [{
                    'fulfillment_line': self.fulfillment['lines'][0]['pk'],
                    'outcome': 'available', 'destination': self.store.pk,
                }],
            }, format='json',
        )
        self.assertEqual(sales_return.status_code, 201, sales_return.data)
        # Outside in: a fulfillment will not reverse while a return or a refund
        # still points at it.
        for collection, document in (
                ('refunds', self.refund), ('returns', sales_return.data),
                ('payments', self.payment), ('fulfillments', self.fulfillment)):
            appended = self.reverse(collection, document['pk'])
            self.assertEqual(appended.status_code, 201, appended.data)
        cases = {
            'refunds': ('refund', Refund),
            'returns': ('sales_return', SalesReturn),
            'payments': ('payment', Payment),
            'fulfillments': ('fulfillment', Fulfillment),
        }
        for collection, (field, model) in cases.items():
            reversal = model.objects.filter(reversal_of__isnull=False).first()
            with self.subTest(document=collection):
                response = self.reverse(collection, reversal.pk)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)

    def test_a_document_is_reversed_only_once(self):
        first = self.reverse('refunds', self.refund['pk'])
        self.assertEqual(first.status_code, 201, first.data)
        second = self.reverse('refunds', self.refund['pk'], reason='Again.')
        self.assertEqual(second.status_code, 400, second.data)
        self.assertIn('already reversed', str(second.data))
        self.assertEqual(Refund.objects.count(), 2)

    def test_one_operation_key_appends_one_reversal(self):
        key = str(uuid4())
        payload = {'operation_key': key, 'reason': 'Entered in error.'}
        url = (
            f"{self.orders_url}{self.order['pk']}"
            f"/refunds/{self.refund['pk']}/reverse/"
        )
        first = self.client.post(url, payload, format='json')
        second = self.client.post(url, payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['pk'], first.data['pk'])
        self.assertEqual(Refund.objects.count(), 2)

    def test_dependent_documents_are_reversed_from_the_outside_in(self):
        """Cash cannot be unwound while a refund still points at it."""
        blocked = self.reverse('payments', self.payment['pk'])
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn('Reverse linked refunds first.', str(blocked.data))
        blocked = self.reverse('fulfillments', self.fulfillment['pk'])
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn('Reverse linked refunds first.', str(blocked.data))
        self.assertEqual(self.reverse('refunds', self.refund['pk']).status_code, 201)
        self.assertEqual(self.reverse('payments', self.payment['pk']).status_code, 201)
        self.assertEqual(
            self.reverse('fulfillments', self.fulfillment['pk']).status_code, 201,
        )
        detail = self.client.get(f"{self.orders_url}{self.order['pk']}/")
        self.assertEqual(detail.data['status'], SalesOrder.Status.CONFIRMED)
        self.assertEqual(detail.data['commerce']['net_paid_total'], '0.0000')


class AllocationStatusMachineTests(CommerceFixtureTestCase):
    """Every reservation state either resolves its stock or has a way on.

    Task 91's invariant again, one level down. A reservation that is neither
    resolved nor able to move is a plant held out of saleable stock forever
    with no operator action able to free it.
    """

    Status = SalesOrderAllocation.Status

    # state -> the states one operator action can move it to.
    TRANSITIONS = {
        Status.PENDING: {Status.RESERVED},
        Status.RESERVED: {Status.RELEASED, Status.EXPIRED, Status.FULFILLED},
        Status.FULFILLED: {Status.RETURNED, Status.RESERVED},
        Status.RETURNED: {Status.FULFILLED},
        Status.RELEASED: set(),
        Status.EXPIRED: set(),
    }

    # Released and expired reservations are resolved: the stock is back in
    # general availability and a replacement reservation is what claims it
    # again, so there is deliberately no route back into the same row.
    RESOLVED = {Status.RELEASED, Status.EXPIRED}

    def states_without_exits(self, transitions):
        """Return unresolved states no operator action can move on from."""
        return {
            state for state, targets in transitions.items()
            if state not in self.RESOLVED and not (targets - {state})
        }

    def test_no_unresolved_reservation_state_is_a_dead_end(self):
        """A plant is never held by a reservation nobody can act on."""
        self.assertEqual(self.states_without_exits(self.TRANSITIONS), set())

    def test_a_state_added_without_an_exit_is_reported(self):
        """The invariant catches the omission rather than assuming care."""
        stuck = {**self.TRANSITIONS, self.Status.RETURNED: set()}
        self.assertEqual(
            self.states_without_exits(stuck), {self.Status.RETURNED},
        )

    def test_every_declared_state_is_a_real_choice(self):
        """The table cannot describe a state the model does not have."""
        named = set(self.TRANSITIONS) | set().union(*self.TRANSITIONS.values())
        self.assertEqual(named, set(SalesOrderAllocation.Status.values))

    def status_of(self, allocation_pk):
        """Read one reservation's current state back from the database."""
        return SalesOrderAllocation.objects.get(pk=allocation_pk).status

    def close(self, order, allocation_pk, action_name):
        """Release or expire one standing reservation."""
        response = self.client.post(
            f"{self.orders_url}{order['pk']}/{action_name}/",
            {'allocations': [allocation_pk], 'reason': 'No longer wanted.'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_one_walk_reaches_every_reservation_state(self):
        """Each declared transition is taken once, against the real API."""
        first = self.available_plant()
        plants = [first, self.available_plant(
            batch=first.batch, cell_planting=first.cell_planting,
        )]
        created = self.client.post(self.orders_url, {'status': 'draft'}, format='json')
        order = created.data
        line = self.client.post(self.lines_url, {
            'order': order['pk'], 'line_type': 'seedling',
            'variety': first.batch.variety_id,
            'description': 'Sale seedlings', 'quantity': 2,
            'unit_price': '10.0000', 'tax_rate': '15.0000',
            'discount_type': 'fixed', 'discount_value': '1.0000',
        }, format='json')
        allocated = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {
                'line': line.data['pk'],
                'plant_ids': [plant.pk for plant in plants],
                # Expiry is explicit rather than time-driven, but the action
                # still refuses a reservation whose term has not run out.
                'expires_at': '2020-01-01T00:00:00Z',
            },
            format='json',
        )
        released, kept = (row['pk'] for row in allocated.data)
        self.assertEqual(self.status_of(kept), self.Status.PENDING)

        self.client.post(f"{self.orders_url}{order['pk']}/confirm/", {}, format='json')
        self.assertEqual(self.status_of(kept), self.Status.RESERVED)

        self.close(order, released, 'release')
        self.assertEqual(self.status_of(released), self.Status.RELEASED)

        fulfillment = self.fulfill(order, [kept])
        self.assertEqual(self.status_of(kept), self.Status.FULFILLED)

        returned = self.client.post(f"{self.orders_url}{order['pk']}/returns/", {
            'operation_key': str(uuid4()), 'reason': 'Customer changed plans.',
            'items': [{
                'fulfillment_line': fulfillment['lines'][0]['pk'],
                'outcome': 'available', 'destination': self.store.pk,
            }],
        }, format='json')
        self.assertEqual(returned.status_code, 201, returned.data)
        self.assertEqual(self.status_of(kept), self.Status.RETURNED)

        reversed_return = self.client.post(
            f"{self.orders_url}{order['pk']}/returns/{returned.data['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Return entered in error.'},
            format='json',
        )
        self.assertEqual(reversed_return.status_code, 201, reversed_return.data)
        self.assertEqual(self.status_of(kept), self.Status.FULFILLED)

        reversed_fulfillment = self.client.post(
            f"{self.orders_url}{order['pk']}/fulfillments/{fulfillment['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Dispatch entered in error.'},
            format='json',
        )
        self.assertEqual(
            reversed_fulfillment.status_code, 201, reversed_fulfillment.data,
        )
        self.assertEqual(self.status_of(kept), self.Status.RESERVED)

        self.close(order, kept, 'expire')
        self.assertEqual(self.status_of(kept), self.Status.EXPIRED)

    def test_a_resolved_reservation_is_replaced_rather_than_revived(self):
        """Released and expired rows stay closed; a new reservation claims it."""
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        allocation = allocations[0]['pk']
        self.close(order, allocation, 'release')

        refused = self.client.post(f"{self.orders_url}{order['pk']}/fulfillments/", {
            'operation_key': str(uuid4()), 'allocation_ids': [allocation],
        }, format='json')
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertEqual(self.status_of(allocation), self.Status.RELEASED)

        replacement = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': order['lines'][0]['pk'], 'plant_ids': [plant.pk]},
            format='json',
        )
        self.assertEqual(replacement.status_code, 201, replacement.data)
        self.assertNotEqual(replacement.data[0]['pk'], allocation)
        self.assertEqual(
            self.status_of(replacement.data[0]['pk']), self.Status.RESERVED,
        )
