"""Tests for the plant lifecycle REST contract and outcome actions."""
# pylint: disable=duplicate-code
from django.utils import timezone

from tests.api import RESTContractTestCase
from tests.factories import (
    make_garden_square,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)

from .lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    record_lifecycle_event,
)
from .models import PlantLifecycleEvent, SpecificPlantLocation


class PlantLifecycleRESTTestCase(RESTContractTestCase):
    """Shared fixture of one tray-raised plant in a tracked location."""

    def setUp(self):
        super().setUp()
        self.packet = make_seed_packet()
        self.tray = make_seed_tray()
        self.cell = make_seed_tray_cell(tray=self.tray)
        self.tray_planting = make_seed_tray_planting(
            seeds_used=self.packet,
            quantity=4,
            seed_tray=self.tray,
        )
        self.cell_planting = make_seed_tray_cell_planting(
            seed_tray_planting=self.tray_planting,
            cell=self.cell,
            quantity=4,
        )
        self.plant = make_specific_plant(cell_planting=self.cell_planting)
        self.location = make_specific_plant_location(specific_plant=self.plant)

    def post_outcome(self, plant_pk, outcome, payload=None):
        """Post one named outcome action for a plant."""
        return self.client.post(
            f'/plantings/specificplants/{plant_pk}/{outcome}/',
            payload or {},
            format='json',
        )

    def get_plant(self, plant_pk):
        """Return one plant through its detail route."""
        response = self.client.get(f'/plantings/specificplants/{plant_pk}/')
        self.assertEqual(response.status_code, 200)
        return response.data


class PlantLifecycleContractTests(PlantLifecycleRESTTestCase):
    """The event collections follow the shared read-only REST contract."""

    @property
    def list_urls(self):
        """Return every lifecycle collection registered with the router."""
        return (
            '/plantings/lifecycle-events/',
            f'/plantings/specificplants/{self.plant.pk}/lifecycle-events/',
        )

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list lifecycle history."""
        self.assert_authentication_required(self.list_urls)

    def test_list_routes_return_lists(self):
        """Authenticated lifecycle collections use the common list contract."""
        self.assert_list_contract(self.list_urls)

    def test_events_cannot_be_written_directly(self):
        """Facts arrive through named actions, never a generic create."""
        response = self.client.post(
            '/plantings/lifecycle-events/',
            {
                'plant': self.plant.pk,
                'event_type': EventType.FAILED,
                'occurred_at': timezone.now(),
            },
            format='json',
        )
        self.assertEqual(response.status_code, 405)

    def test_events_cannot_be_updated_or_deleted(self):
        """Recorded facts have no generic update or delete endpoint."""
        event = PlantLifecycleEvent.objects.create(
            plant=self.plant,
            batch=self.tray_planting.batch,
            event_type=EventType.GERMINATED,
            occurred_at=self.plant.germinated,
        )
        url = f'/plantings/lifecycle-events/{event.pk}/'
        self.assertEqual(self.client.patch(url, {}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_events_can_be_filtered(self):
        """The screens can narrow history by plant, batch, and type."""
        self.post_outcome(self.plant.pk, 'ready')
        other = make_specific_plant()
        response = self.client.get(
            f'/plantings/lifecycle-events/?plant={self.plant.pk}'
            f'&event_type={EventType.READY}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['plant'], self.plant.pk)

        response = self.client.get(f'/plantings/lifecycle-events/?plant={other.pk}')
        self.assertEqual(response.data, [])

    def test_an_invalid_filter_is_rejected(self):
        """A malformed filter reports a field error instead of ignoring it."""
        response = self.client.get('/plantings/lifecycle-events/?event_type=nonsense')
        self.assertEqual(response.status_code, 400)
        self.assertIn('event_type', response.data)


class GerminationRecordsHistoryTests(PlantLifecycleRESTTestCase):
    """Recording a germination starts the plant's lifecycle history."""

    def test_creating_a_plant_records_its_germination(self):
        """The existing germination action now also appends the fact."""
        germinated = timezone.now()
        response = self.client.post(
            '/plantings/specificplants/',
            {
                'cell_planting': self.cell_planting.pk,
                'germinated': germinated,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['lifecycle_state'], LifecycleState.GROWING)

        events = PlantLifecycleEvent.objects.filter(plant_id=response.data['pk'])
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.event_type, EventType.GERMINATED)
        self.assertEqual(event.occurred_at, germinated)
        self.assertEqual(event.batch_id, self.tray_planting.batch_id)
        self.assertEqual(event.created_by_id, self.user.pk)

    def test_moving_a_plant_out_records_a_transplant(self):
        """A move into a garden square is also a planting out."""
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            {
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'garden_square': make_garden_square().pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=EventType.TRANSPLANTED,
            ).exists(),
        )

    def test_moving_between_cells_records_no_transplant(self):
        """Rearranging a tray is not a planting out."""
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            {
                'location_type': SpecificPlantLocation.SEED_TRAY_CELL,
                'seed_tray_cell': make_seed_tray_cell(tray=self.tray, x_position=1).pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=EventType.TRANSPLANTED,
            ).exists(),
        )

    def test_a_resolved_plant_cannot_be_moved(self):
        """A culled plant is not planted out afterwards."""
        self.post_outcome(self.plant.pk, 'cull')
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/move/',
            {
                'location_type': SpecificPlantLocation.GARDEN_SQUARE,
                'garden_square': make_garden_square().pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class PlantOutcomeActionTests(PlantLifecycleRESTTestCase):
    """Each explicit outcome action appends one auditable event."""

    def test_every_outcome_action_records_its_event(self):
        """The named actions cover the recordable dispositions.

        The backward facts carry the state they are recorded from, because
        holding back names an offer and ending a retention names a retention.
        """
        actions = {
            'ready': ((), EventType.READY),
            'retain': ((), EventType.RETAINED),
            'fail': ((), EventType.FAILED),
            'cull': ((), EventType.CULLED),
            'donate': ((), EventType.DONATED),
            'finish-harvest': ((), EventType.HARVEST_FINISHED),
            'hold-back': (('ready',), EventType.HELD_BACK),
            'end-retention': (('retain',), EventType.RETENTION_ENDED),
        }
        for outcome, (priors, event_type) in actions.items():
            with self.subTest(outcome=outcome):
                plant = make_specific_plant()
                for prior in priors:
                    self.assertEqual(
                        self.post_outcome(plant.pk, prior).status_code, 201,
                    )
                response = self.post_outcome(
                    plant.pk,
                    outcome,
                    {'reason': 'Recorded by test.'},
                )
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['event_type'], event_type)
                self.assertEqual(response.data['plant'], plant.pk)
                self.assertEqual(response.data['reason'], 'Recorded by test.')

    def test_a_plant_reports_its_derived_state(self):
        """Derived state travels with the plant, not as a stored field."""
        self.post_outcome(self.plant.pk, 'ready')
        data = self.get_plant(self.plant.pk)
        self.assertEqual(data['lifecycle_state'], LifecycleState.AVAILABLE)
        self.assertTrue(data['sellable'])
        self.assertIsNone(data['final_outcome'])

        self.post_outcome(self.plant.pk, 'retain')
        data = self.get_plant(self.plant.pk)
        self.assertEqual(data['lifecycle_state'], LifecycleState.RETAINED)
        self.assertFalse(data['sellable'])
        self.assertEqual(data['final_outcome'], EventType.RETAINED)
        self.assertIsNotNone(data['final_outcome_at'])

    def test_a_plant_detail_lists_its_history_in_order(self):
        """The detail view carries the chronological lifecycle history."""
        self.post_outcome(self.plant.pk, 'ready')
        self.post_outcome(self.plant.pk, 'cull')
        data = self.get_plant(self.plant.pk)
        self.assertEqual(
            [event['event_type'] for event in data['lifecycle_events']],
            [EventType.READY, EventType.CULLED],
        )

    def test_an_invalid_transition_returns_a_field_error(self):
        """A rejected outcome explains itself without changing anything."""
        self.post_outcome(self.plant.pk, 'fail')
        response = self.post_outcome(self.plant.pk, 'ready')
        self.assertEqual(response.status_code, 400)
        self.assertIn('event_type', response.data)

    def test_an_invalid_transition_leaves_the_location_alone(self):
        """A refused outcome never partially closes a location."""
        self.post_outcome(self.plant.pk, 'retain')
        response = self.post_outcome(self.plant.pk, 'ready')
        self.assertEqual(response.status_code, 400)
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)

    def test_a_final_outcome_closes_the_active_location(self):
        """Leaving the operation ends the plant's occupancy in one step."""
        occurred_at = timezone.now()
        response = self.post_outcome(
            self.plant.pk,
            'donate',
            {'occurred_at': occurred_at},
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.location.refresh_from_db()
        self.assertEqual(self.location.ended, occurred_at)


class PlantEventReversalActionTests(PlantLifecycleRESTTestCase):
    """A mistaken fact is corrected by appending its reversal."""

    def test_a_failure_can_be_reversed(self):
        """The original stays visible and the state recovers."""
        failure = self.post_outcome(self.plant.pk, 'fail').data
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/reverse-event/',
            {'event': failure['pk'], 'reason': 'Recorded against the wrong plant.'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['event_type'], EventType.CORRECTED)
        self.assertEqual(response.data['reversal_of'], failure['pk'])

        data = self.get_plant(self.plant.pk)
        self.assertEqual(data['lifecycle_state'], LifecycleState.GROWING)
        self.assertIsNone(data['final_outcome'])
        history = {event['pk']: event for event in data['lifecycle_events']}
        self.assertIn(failure['pk'], history)
        self.assertEqual(history[failure['pk']]['reversed_by'], response.data['pk'])

    def test_a_reversal_requires_a_reason(self):
        """Audited corrections always say why they were needed."""
        failure = self.post_outcome(self.plant.pk, 'fail').data
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/reverse-event/',
            {'event': failure['pk'], 'reason': ''},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', response.data)

    def test_an_event_from_another_plant_cannot_be_reversed(self):
        """A correction only ever names one of its own plant's facts."""
        other = make_specific_plant()
        failure = self.post_outcome(other.pk, 'fail').data
        response = self.client.post(
            f'/plantings/specificplants/{self.plant.pk}/reverse-event/',
            {'event': failure['pk'], 'reason': 'Wrong plant.'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)


class BackwardTransitionActionTests(PlantLifecycleRESTTestCase):
    """Withdrawing stock is offered beside the correction, not through it."""

    def test_holding_back_returns_the_plant_to_production(self):
        """The plant stops being sellable without becoming resolved."""
        self.post_outcome(self.plant.pk, 'ready')
        response = self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        data = self.get_plant(self.plant.pk)
        self.assertEqual(data['lifecycle_state'], LifecycleState.GROWING)
        self.assertFalse(data['sellable'])
        self.assertIsNone(data['final_outcome'])

    def test_holding_back_leaves_the_ready_fact_uncorrected(self):
        """This is the whole distinction: the plant really was ready."""
        ready = self.post_outcome(self.plant.pk, 'ready').data
        self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        history = {
            event['pk']: event
            for event in self.get_plant(self.plant.pk)['lifecycle_events']
        }
        self.assertIsNone(history[ready['pk']]['reversed_by'])
        self.assertEqual(
            [event['event_type'] for event in history.values()],
            [EventType.READY, EventType.HELD_BACK],
        )

    def test_a_backward_action_requires_a_reason(self):
        """The API refuses an unexplained withdrawal, as the service does."""
        for outcome, prior in (('hold-back', 'ready'), ('end-retention', 'retain')):
            with self.subTest(outcome=outcome):
                plant = make_specific_plant()
                self.post_outcome(plant.pk, prior)
                response = self.post_outcome(plant.pk, outcome)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('reason', response.data)

    def test_a_backward_action_is_refused_from_the_wrong_state(self):
        """Only stock on offer can be held back."""
        response = self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('event_type', response.data)

    def test_a_backward_action_leaves_the_location_alone(self):
        """Withdrawing stock changes the plan for it, not its bench."""
        self.post_outcome(self.plant.pk, 'ready')
        self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        self.location.refresh_from_db()
        self.assertIsNone(self.location.ended)

    def test_the_actions_each_state_accepts_are_the_ones_the_screen_offers(self):
        """This repository has no JavaScript test runner, so the matrix that
        `ACTION_STATES` in `frontend/js/plantings/lifecycle.tsx` mirrors is
        pinned here. A button offered for a state the server refuses returns a
        400 with nothing on screen explaining it, which is how a quarantined
        plant came to show **Ready** and **Donate**.
        """
        histories = {
            'growing': (),
            'available': (EventType.READY,),
            'retained': (EventType.RETAINED,),
            'quarantined': (
                EventType.READY, EventType.SOLD, EventType.RETURNED_QUARANTINED,
            ),
        }
        accepted = {
            'growing': {'ready', 'retain', 'finish-harvest', 'fail', 'cull', 'donate'},
            'available': {
                'hold-back', 'retain', 'finish-harvest', 'fail', 'cull', 'donate',
            },
            'retained': {'end-retention', 'finish-harvest', 'fail', 'cull', 'donate'},
            'quarantined': {'retain', 'fail', 'cull'},
        }
        every_action = {
            'ready', 'hold-back', 'retain', 'end-retention',
            'finish-harvest', 'fail', 'cull', 'donate',
        }
        for state, priors in histories.items():
            for outcome in sorted(every_action):
                with self.subTest(state=state, outcome=outcome):
                    plant = make_specific_plant()
                    for prior in priors:
                        record_lifecycle_event(
                            plant, self.user, OutcomeRequest(prior),
                        )
                    response = self.post_outcome(
                        plant.pk, outcome, {'reason': 'Recorded by test.'},
                    )
                    expected = 201 if outcome in accepted[state] else 400
                    self.assertEqual(response.status_code, expected, response.data)

    def test_the_detail_reports_every_span_the_plant_was_offered(self):
        """Only the latest state would hide the second offer entirely."""
        self.post_outcome(self.plant.pk, 'ready')
        self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        self.post_outcome(self.plant.pk, 'ready')
        data = self.get_plant(self.plant.pk)
        intervals = data['availability_intervals']
        self.assertEqual(len(intervals), 2)
        self.assertIsNotNone(intervals[0]['ended'])
        self.assertIsNone(intervals[1]['ended'])
        self.assertEqual(data['first_ready_at'], intervals[0]['started'])
        self.assertEqual(data['state_since'], intervals[1]['started'])

    def test_a_held_back_plant_is_offered_again_by_grading_it(self):
        """A repeated cycle is three facts, not one fact recorded twice."""
        self.post_outcome(self.plant.pk, 'ready')
        self.post_outcome(
            self.plant.pk, 'hold-back', {'reason': 'Gone leggy in the heat.'},
        )
        self.assertEqual(self.post_outcome(self.plant.pk, 'ready').status_code, 201)
        data = self.get_plant(self.plant.pk)
        self.assertEqual(data['lifecycle_state'], LifecycleState.AVAILABLE)
        self.assertEqual(
            [event['event_type'] for event in data['lifecycle_events']],
            [EventType.READY, EventType.HELD_BACK, EventType.READY],
        )


class BulkPlantOutcomeActionTests(PlantLifecycleRESTTestCase):
    """A selected list of plants yields one event per plant."""

    def setUp(self):
        super().setUp()
        self.others = [make_specific_plant() for _ in range(2)]
        self.plant_ids = [self.plant.pk] + [plant.pk for plant in self.others]

    def _post_bulk(self, payload):
        """Post one bulk outcome request."""
        return self.client.post(
            '/plantings/specificplants/bulk-outcome/',
            payload,
            format='json',
        )

    def test_each_selected_plant_gets_its_own_event(self):
        """The aggregate action stays traceable plant by plant."""
        response = self._post_bulk({
            'plants': self.plant_ids,
            'event_type': EventType.CULLED,
            'reason': 'Cleared the bench.',
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), len(self.plant_ids))
        self.assertEqual(
            sorted(event['plant'] for event in response.data),
            sorted(self.plant_ids),
        )
        for event in response.data:
            self.assertEqual(event['event_type'], EventType.CULLED)

    def test_an_invalid_member_rejects_the_whole_selection(self):
        """A partial application would leave an unexplainable audit trail."""
        self.post_outcome(self.others[0].pk, 'fail')
        response = self._post_bulk({
            'plants': self.plant_ids,
            'event_type': EventType.CULLED,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('plants', response.data)
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=EventType.CULLED,
            ).count(),
            0,
        )

    def test_an_unknown_plant_is_rejected(self):
        """A selection naming a plant we cannot see records nothing."""
        response = self._post_bulk({
            'plants': self.plant_ids + [0],
            'event_type': EventType.CULLED,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('plants', response.data)

    def test_an_empty_selection_is_rejected(self):
        """Recording an outcome for nothing is a mistake, not a no-op."""
        response = self._post_bulk({
            'plants': [],
            'event_type': EventType.CULLED,
        })
        self.assertEqual(response.status_code, 400)

    def test_a_non_outcome_event_type_is_rejected(self):
        """Germination and corrections are not bulk-recordable outcomes."""
        response = self._post_bulk({
            'plants': self.plant_ids,
            'event_type': EventType.GERMINATED,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('event_type', response.data)

    def test_a_backward_fact_is_bulk_recordable_with_a_reason(self):
        """Grading a whole batch down is the case this exists for."""
        for plant_id in self.plant_ids:
            self.post_outcome(plant_id, 'ready')
        response = self._post_bulk({
            'plants': self.plant_ids,
            'event_type': EventType.HELD_BACK,
            'reason': 'The whole batch has gone leggy.',
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), len(self.plant_ids))
        for event in response.data:
            self.assertEqual(event['event_type'], EventType.HELD_BACK)

    def test_a_bulk_backward_fact_without_a_reason_records_nothing(self):
        """The reason rule holds for the selection as it does for one plant."""
        for plant_id in self.plant_ids:
            self.post_outcome(plant_id, 'ready')
        response = self._post_bulk({
            'plants': self.plant_ids,
            'event_type': EventType.HELD_BACK,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('plants', response.data)
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                event_type=EventType.HELD_BACK,
            ).exists(),
        )
