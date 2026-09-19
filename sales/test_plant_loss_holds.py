"""A lifecycle fact that ends a held plant's availability ends the hold too.

Task 125: a loss releases the hold and says why in the reservation history,
a decision not yet acted on is refused until somebody releases the hold, and a
dispatch that still reaches a plant nobody can sell says what became of it.
"""

# pylint: disable=duplicate-code,missing-function-docstring,too-many-ancestors

from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from health.models import QuarantineAction
from health.operations import act_on_quarantine
from plantings.bulk_operations import concrete_request, execute_bulk_operation
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    REFUSED_UNDER_HOLD,
    RELEASES_HOLD,
    STATE_AFTER,
    plant_lifecycle_summary,
    record_bulk_outcome,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from plantings.models import BulkPlantOperation, PlantLifecycleEvent
from plantings.register import RegisterFilters, register_queryset
from tests.factories import quarantine_stock, reserve_plants

from .expiry import expire_due_reservations
from .models import ReservationEvent, SalesOrder, SalesOrderAllocation
from .test_commerce import CommerceFixtureTestCase


class PlantLossHoldTestCase(CommerceFixtureTestCase):
    """One available plant held by one confirmed order."""

    plants_url = '/plantings/specificplants/'

    def held(self, expires_at=None, count=1):
        plants = [self.available_plant()]
        plants += [self.available_plant(cell_planting=plants[0].cell_planting) for _ in range(count - 1)]
        order, allocations = reserve_plants(
            self.workspace, self.user, plants, expires_at=expires_at,
        )
        return order, plants, allocations

    def outcome(self, plant, outcome, reason='Aphids through the whole bench.'):
        return self.client.post(
            f'{self.plants_url}{plant.pk}/{outcome}/', {'reason': reason}, format='json',
        )

    def local_day(self, moment):
        return moment.astimezone(ZoneInfo(self.workspace.timezone)).date().isoformat()

    def assert_released_by(self, allocation, plant, label):
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RELEASED)
        event = allocation.events.get(event_type=ReservationEvent.EventType.RELEASED)
        loss = PlantLifecycleEvent.objects.filter(plant=plant).latest('pk')
        day = self.local_day(loss.occurred_at)
        self.assertIn(
            f'plant {plant.pk} was recorded as {label} on {day} '
            f'(lifecycle event {loss.pk})',
            event.reason,
        )
        return event


class LossReleasesHoldTests(PlantLossHoldTestCase):
    """A loss has already happened, so the promise it breaks ends with it."""

    def test_a_culled_plant_is_released_named_and_no_longer_counted_reserved(self):
        order, (plant,), (allocation,) = self.held()

        response = self.outcome(plant, 'cull')

        self.assertEqual(response.status_code, 201, response.data)
        event = self.assert_released_by(allocation, plant, 'culled')
        self.assertIn('Reason given: Aphids through the whole bench.', event.reason)
        self.assertEqual(event.created_by, self.user)
        self.assertFalse(
            register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk).reserved,
        )
        self.assertFalse(
            register_queryset(self.workspace, RegisterFilters(reserved=True))
            .filter(pk=plant.pk).exists(),
        )
        order = self.client.get(f"{self.orders_url}{order.pk}/").data
        self.assertEqual(order['status'], SalesOrder.Status.CONFIRMED)
        self.assertEqual(
            [row['status'] for row in order['lines'][0]['allocations']],
            [SalesOrderAllocation.Status.RELEASED],
        )

    def test_an_open_ended_hold_the_sweep_never_touches_is_released(self):
        _order, (plant,), (allocation,) = self.held(expires_at=None)
        self.assertEqual(expire_due_reservations(self.workspace), [])

        response = self.outcome(plant, 'fail')

        self.assertEqual(response.status_code, 201, response.data)
        self.assert_released_by(allocation, plant, 'failed')

    def test_every_loss_releases_the_hold(self):
        self.assertEqual(RELEASES_HOLD, {
            EventType.FAILED, EventType.LOST, EventType.CULLED,
            EventType.DONATED, EventType.HARVEST_FINISHED,
        })
        for event_type in sorted(RELEASES_HOLD):
            with self.subTest(event_type=event_type):
                _order, (plant,), (allocation,) = self.held(
                    expires_at=timezone.now() + timedelta(days=3),
                )
                record_lifecycle_event(
                    plant, self.user, OutcomeRequest(event_type, reason='Gone.'),
                )
                self.assert_released_by(
                    allocation, plant,
                    LifecycleState(STATE_AFTER[event_type]).label.lower(),
                )

    def test_a_bulk_loss_releases_every_hold_in_the_selection(self):
        _order, plants, allocations = self.held(count=2)
        free = self.available_plant()

        record_bulk_outcome(
            [plant.pk for plant in [*plants, free]], self.user,
            OutcomeRequest(EventType.LOST, reason='Bench blew over.'),
        )

        for plant, allocation in zip(plants, allocations):
            self.assert_released_by(allocation, plant, 'lost')

    def test_the_rest_of_the_order_still_dispatches_and_the_line_can_be_refilled(self):
        order, (lost, kept), allocations = self.held(count=2)

        self.assertEqual(self.outcome(lost, 'cull').status_code, 201)
        fulfilled = self.fulfill({'pk': order.pk}, [allocations[1].pk])

        self.assertEqual(len(fulfilled['lines']), 1)
        data = self.client.get(f"{self.orders_url}{order.pk}/").data
        self.assertEqual(data['status'], SalesOrder.Status.PARTIALLY_FULFILLED)
        self.assertEqual(
            plant_lifecycle_summary(kept).state, LifecycleState.SOLD,
        )
        substitute = self.available_plant(cell_planting=kept.cell_planting)
        allocated = self.client.post(
            f"{self.orders_url}{order.pk}/allocate/",
            {'line': data['lines'][0]['pk'], 'plant_ids': [substitute.pk]},
            format='json',
        )
        self.assertEqual(allocated.status_code, 201, allocated.data)

    def test_a_quarantine_cull_releases_the_hold(self):
        _order, (plant,), (allocation,) = self.held()
        case = quarantine_stock(
            self.workspace, self.user, [{'type': 'plant', 'id': plant.pk}],
        )

        act_on_quarantine(
            self.workspace, self.user, case,
            action_name=QuarantineAction.Action.CULL,
            idempotency_key=uuid4(), reason='Confirmed virus.',
        )

        self.assert_released_by(allocation, plant, 'culled')

    def test_a_bulk_plant_operation_cull_releases_the_hold(self):
        _order, (plant,), (allocation,) = self.held()

        execute_bulk_operation(self.workspace, self.user, concrete_request(
            idempotency_key=uuid4(),
            action=BulkPlantOperation.Action.CULL,
            atomicity=BulkPlantOperation.Atomicity.ALL_OR_NOTHING,
            occurred_at=timezone.now(),
            reason='Cleared the bench.',
            plants=[plant.pk],
            selection_source={'mode': 'ids'},
            action_payload={},
        ))

        self.assert_released_by(allocation, plant, 'culled')


class DecisionRefusedUnderHoldTests(PlantLossHoldTestCase):
    """A decision not yet acted on waits for somebody to release the hold."""

    def test_holding_back_names_the_order_and_goes_through_once_released(self):
        order, (plant,), (allocation,) = self.held()

        refused = self.outcome(plant, 'hold-back', 'Gone leggy.')

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn(order.order_number, str(refused.data))
        self.assertIn('Release that hold', str(refused.data))
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.AVAILABLE)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)

        released = self.client.post(
            f"{self.orders_url}{order.pk}/release/",
            {'allocations': [allocation.pk], 'reason': 'Stock gone leggy.'},
            format='json',
        )
        self.assertEqual(released.status_code, 200, released.data)
        self.assertEqual(self.outcome(plant, 'hold-back', 'Gone leggy.').status_code, 201)

    def test_retaining_a_held_plant_is_refused(self):
        self.assertEqual(REFUSED_UNDER_HOLD, {EventType.HELD_BACK, EventType.RETAINED})
        order, (plant,), _allocations = self.held()

        refused = self.outcome(plant, 'retain', 'Keep as mother stock.')

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn(order.order_number, str(refused.data))

    def test_a_bulk_hold_back_reports_every_held_plant_and_writes_nothing(self):
        order, plants, allocations = self.held(count=2)
        free = self.available_plant()
        before = PlantLifecycleEvent.objects.count()

        response = self.client.post(f'{self.plants_url}bulk-outcome/', {
            'plants': [free.pk, *[plant.pk for plant in plants]],
            'event_type': EventType.HELD_BACK,
            'reason': 'Gone leggy.',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        messages = response.data['plants']
        self.assertEqual(len(messages), 2, messages)
        for plant, message in zip(plants, messages):
            self.assertIn(f'Plant {plant.pk}:', message)
            self.assertIn(order.order_number, message)
        self.assertEqual(PlantLifecycleEvent.objects.count(), before)
        for allocation in allocations:
            allocation.refresh_from_db()
            self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)

    def test_striking_out_the_ready_under_a_hold_is_refused(self):
        order, (plant,), _allocations = self.held()
        ready = plant.lifecycle_events.get(event_type=EventType.READY)

        with self.assertRaisesMessage(ValidationError, order.order_number):
            reverse_lifecycle_event(ready, self.user, 'Never graded.')


class DispatchSaysWhatBecameOfThePlantTests(PlantLossHoldTestCase):
    """A hold older than the fix fails at dispatch with a diagnosis."""

    def test_a_hold_left_on_a_culled_plant_names_the_cull(self):
        order, (plant,), (allocation,) = self.held()
        # Written directly, as a deployed hold that predates the release was.
        culled = PlantLifecycleEvent.objects.create(
            workspace=self.workspace, plant=plant, batch=plant.batch,
            event_type=EventType.CULLED, occurred_at=timezone.now(),
            reason='Botrytis.',
        )

        response = self.client.post(
            f"{self.orders_url}{order.pk}/fulfillments/",
            {'operation_key': str(uuid4()), 'allocation_ids': [allocation.pk]},
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        day = self.local_day(culled.occurred_at)
        self.assertEqual(response.data['allocations'], [
            f'Plant {plant.pk} was recorded as culled on {day} (Botrytis.).',
            'Release those holds and allocate other plants, or record a shortfall.',
        ])
