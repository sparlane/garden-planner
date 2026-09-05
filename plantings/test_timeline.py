"""Contracts for the one chronological reading of a plant's whole history."""

# Test names state their behavior; repeating it in method docstrings adds noise.
# pylint: disable=missing-function-docstring,duplicate-code

from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from health.models import QuarantineAction
from health.operations import act_on_quarantine
from sales.models import ReservationEvent
from sales.services import allocate_targets, confirm_order, create_order
from sales.models import SalesOrderLine
from tests.api import RESTContractTestCase
from tests.factories import (
    make_nursery_workspace,
    make_production_batch,
    make_seed_tray_planting,
    make_specific_plant,
    quarantine_stock,
)
from workspaces.models import get_current_workspace

from .cohorts import change_cohort, observe_cohort, promote_cohort
from .growth import correct_observation, record_observation
from .lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from .models import CohortOperation, GrowthStage, NurseryObservation, PlantCohort
from .timeline import Source, plant_timeline, timeline_rows


class PlantTimelineFixture(TestCase):
    """A nursery workspace with the four histories a plant accumulates."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.batch = make_production_batch()
        self.sowing = make_seed_tray_planting(batch=self.batch)
        self.start = timezone.now() - timedelta(days=90)

    def germinated_plant(self, **overrides):
        """Create a plant germinated at the fixture's start, with its fact."""
        overrides.setdefault('germinated', self.start)
        plant = make_specific_plant(**overrides)
        record_germination_event(plant, None)
        return plant

    def offer(self, plant, when):
        """Grade one plant ready at a stated time."""
        return record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, occurred_at=when),
        )

    def promise(self, plant, confirm=True):
        """Promise one plant to a fresh order, reserving it when confirmed."""
        order = create_order(self.workspace, None, status='draft')
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=plant.batch.variety,
            description='Sale seedlings',
            quantity=1,
            unit_price='10.0000',
            tax_rate='15.0000',
        )
        allocations = allocate_targets(line, None, plant_ids=[plant.pk])
        if confirm:
            confirm_order(order, None)
        return order, allocations[0]

    def kinds(self, entries, source):
        """Return the kinds one source contributed, in timeline order."""
        return [entry.kind for entry in entries if entry.source == source]


class PlantTimelineProjectionTests(PlantTimelineFixture):
    """Four separate histories read as one sequence without being merged."""

    def test_every_source_appears_once_each_in_one_order(self):
        plant = self.germinated_plant()
        record_observation(
            self.workspace, None, plant_ids=[plant.pk],
            height_cm='12.500', occurred_at=self.start + timedelta(days=1),
        )
        self.offer(plant, self.start + timedelta(days=2))
        self.promise(plant, confirm=False)
        case = quarantine_stock(self.workspace, None, [{'type': 'plant', 'id': plant.pk}])

        entries = plant_timeline(plant)

        self.assertEqual(
            sorted({entry.source for entry in entries}),
            [Source.ALLOCATION, Source.LIFECYCLE, Source.OBSERVATION, Source.QUARANTINE],
        )
        self.assertEqual(len(entries), len({entry.entry_id for entry in entries}))
        self.assertEqual(
            [entry.occurred_at for entry in entries],
            sorted(entry.occurred_at for entry in entries),
        )
        self.assertEqual(self.kinds(entries, Source.OBSERVATION), ['measured'])
        self.assertEqual(
            self.kinds(entries, Source.QUARANTINE),
            [QuarantineAction.Action.QUARANTINE],
        )
        quarantine = next(e for e in entries if e.source == Source.QUARANTINE)
        self.assertEqual(quarantine.detail['case'], case.pk)

    def test_potting_on_and_grading_are_named_by_what_they_recorded(self):
        plant = self.germinated_plant()
        stage = GrowthStage.objects.get(workspace=self.workspace, code='rooted')
        record_observation(
            self.workspace, None, plant_ids=[plant.pk], stage=stage,
            occurred_at=self.start + timedelta(days=3),
        )
        entries = plant_timeline(plant)
        self.assertEqual(self.kinds(entries, Source.OBSERVATION), ['staged'])
        observation = next(e for e in entries if e.source == Source.OBSERVATION)
        self.assertEqual(observation.detail['stage'], stage.name)
        self.assertEqual(observation.detail['scope'], 'plant')

    def test_the_commercial_claim_shows_the_promise_and_what_became_of_it(self):
        plant = self.germinated_plant()
        self.offer(plant, self.start + timedelta(days=2))
        order, _allocation = self.promise(plant)

        entries = plant_timeline(plant)

        self.assertEqual(self.kinds(entries, Source.ALLOCATION), ['promised'])
        self.assertEqual(
            self.kinds(entries, Source.RESERVATION),
            [ReservationEvent.EventType.RESERVED],
        )
        promise = next(e for e in entries if e.source == Source.ALLOCATION)
        self.assertEqual(promise.detail['order_number'], order.order_number)
        self.assertLess(
            [e.source for e in entries].index(Source.ALLOCATION),
            [e.source for e in entries].index(Source.RESERVATION),
        )

    def test_a_release_from_quarantine_follows_the_quarantine_that_held_it(self):
        plant = self.germinated_plant()
        self.offer(plant, self.start + timedelta(days=2))
        case = quarantine_stock(self.workspace, None, [{'type': 'plant', 'id': plant.pk}])
        act_on_quarantine(
            self.workspace, None, case=case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason='Cleared on review.',
        )

        entries = plant_timeline(plant)

        self.assertEqual(
            self.kinds(entries, Source.QUARANTINE),
            [QuarantineAction.Action.QUARANTINE, QuarantineAction.Action.RELEASE],
        )

    def test_the_projection_writes_nothing(self):
        plant = self.germinated_plant()
        record_observation(
            self.workspace, None, plant_ids=[plant.pk], notes='Looking well.',
        )
        self.offer(plant, self.start + timedelta(days=2))
        with CaptureQueriesContext(connection) as queries:
            plant_timeline(plant)
        written = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        self.assertEqual(written, [])


class PlantTimelineCorrectionTests(PlantTimelineFixture):
    """A struck fact stays on the timeline, marked as struck."""

    def test_a_reversed_lifecycle_fact_is_marked_and_its_reversal_names_it(self):
        plant = self.germinated_plant()
        ready = self.offer(plant, self.start + timedelta(days=2))
        correction = reverse_lifecycle_event(ready, None, 'Graded the wrong plant.')

        entries = {entry.source_id: entry for entry in plant_timeline(plant)
                   if entry.source == Source.LIFECYCLE}

        self.assertTrue(entries[ready.pk].corrected)
        self.assertFalse(entries[correction.pk].corrected)
        self.assertEqual(entries[correction.pk].corrects, entries[ready.pk].entry_id)

    def test_a_replaced_observation_is_marked_and_both_readings_survive(self):
        plant = self.germinated_plant()
        original = record_observation(
            self.workspace, None, plant_ids=[plant.pk], height_cm='12.500',
            occurred_at=self.start + timedelta(days=1),
        )
        replacement = correct_observation(
            self.workspace, None, observation_id=original.pk, height_cm='21.000',
            occurred_at=self.start + timedelta(days=1),
        )

        entries = {entry.source_id: entry for entry in plant_timeline(plant)
                   if entry.source == Source.OBSERVATION}

        self.assertEqual(sorted(entries), sorted([original.pk, replacement.pk]))
        self.assertTrue(entries[original.pk].corrected)
        self.assertEqual(
            entries[replacement.pk].corrects, entries[original.pk].entry_id,
        )
        self.assertEqual(entries[original.pk].detail['height_cm'], '12.500')


class PromotedPlantTimelineTests(PlantTimelineFixture):
    """A plant promoted from a block keeps the block's early life."""

    def promoted_plant(self):
        """Observe a block, take a loss from it, then promote one plant."""
        cohort, _observed = observe_cohort(
            self.workspace, None, batch=self.batch, source_sowing=self.sowing,
            quantity=10, idempotency_key=uuid4(),
            occurred_at=self.start,
        )
        change_cohort(
            self.workspace, None, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS, quantity=2,
            loss_cause=CohortOperation.LossCause.FAILED,
            idempotency_key=uuid4(), reason='Damping off.',
            occurred_at=self.start + timedelta(days=5),
        )
        record_observation(
            self.workspace, None, cohort_id=cohort.pk, notes='Block potted on.',
            occurred_at=self.start + timedelta(days=6),
        )
        cohort.refresh_from_db()
        plants, operation = promote_cohort(
            self.workspace, None, cohort_id=cohort.pk,
            expected_revision=cohort.revision, quantity=1,
            idempotency_key=uuid4(), reason='Sold as an individual.',
            occurred_at=self.start + timedelta(days=10),
        )
        return plants[0], cohort, operation

    def test_cohort_history_comes_before_the_plant_has_an_identity(self):
        plant, cohort, operation = self.promoted_plant()

        entries = plant_timeline(plant)
        sources = [entry.source for entry in entries]

        self.assertEqual(
            self.kinds(entries, Source.COHORT),
            [
                CohortOperation.Action.OBSERVE,
                CohortOperation.Action.LOSS,
                CohortOperation.Action.PROMOTE,
            ],
        )
        self.assertTrue(all(
            entry.detail['cohort'] == cohort.pk
            for entry in entries if entry.source == Source.COHORT
        ))
        self.assertLess(
            max(index for index, source in enumerate(sources) if source == Source.COHORT),
            len(sources),
        )
        self.assertEqual(
            [entry.occurred_at for entry in entries],
            sorted(entry.occurred_at for entry in entries),
        )
        loss = next(e for e in entries if e.kind == CohortOperation.Action.LOSS)
        self.assertEqual(loss.detail['quantity_delta'], -2)
        self.assertEqual(loss.detail['loss_cause'], CohortOperation.LossCause.FAILED)
        self.assertEqual(operation.action, CohortOperation.Action.PROMOTE)

    def test_the_blocks_own_observations_are_scoped_as_the_cohorts(self):
        plant, cohort, _operation = self.promoted_plant()

        cohort_observations = [
            entry for entry in plant_timeline(plant)
            if entry.source == Source.OBSERVATION and entry.detail['scope'] == 'cohort'
        ]

        self.assertTrue(cohort_observations)
        self.assertTrue(all(
            entry.detail['cohort'] == cohort.pk for entry in cohort_observations
        ))

    def test_a_later_cohort_operation_is_not_this_plants_history(self):
        plant, cohort, _operation = self.promoted_plant()
        cohort.refresh_from_db()
        change_cohort(
            self.workspace, None, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS, quantity=1,
            loss_cause=CohortOperation.LossCause.CULLED,
            idempotency_key=uuid4(), reason='Culled after the promotion.',
            occurred_at=self.start + timedelta(days=20),
        )

        entries = plant_timeline(plant)

        self.assertNotIn(
            CohortOperation.LossCause.CULLED,
            [entry.detail.get('loss_cause') for entry in entries],
        )

    def test_a_plant_raised_from_a_tray_has_no_cohort_history(self):
        plant = self.germinated_plant()
        self.assertEqual(self.kinds(plant_timeline(plant), Source.COHORT), [])


class PlantTimelineRESTTests(RESTContractTestCase, PlantTimelineFixture):
    """The endpoint pages one plant's reading and names its sources."""

    def setUp(self):
        super().setUp()
        self.plant = self.germinated_plant()
        self.url = f'/plantings/specificplants/{self.plant.pk}/timeline/'

    def test_the_timeline_is_paged_oldest_first(self):
        for day in range(1, 6):
            record_observation(
                self.workspace, None, plant_ids=[self.plant.pk],
                notes=f'Day {day}.', occurred_at=self.start + timedelta(days=day),
            )

        response = self.client.get(self.url, {'page_size': 2})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 6)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['source'], Source.LIFECYCLE)
        self.assertEqual(response.data['results'][1]['detail']['notes'], 'Day 1.')
        self.assertEqual(response.data['sources'][0], Source.COHORT)

    def test_the_screen_and_the_traceability_report_agree(self):
        record_observation(
            self.workspace, None, plant_ids=[self.plant.pk], notes='Potted on.',
        )
        response = self.client.get(self.url, {'page_size': 500})
        report = self.client.get(f'/reports/traceability/plants/{self.plant.pk}/')

        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(
            [row['entry_id'] for row in response.data['results']],
            [row['entry_id'] for row in report.data['results'][0]['timeline']],
        )
        self.assertEqual(
            report.data['totals']['timeline_entries'], response.data['count'],
        )

    def test_a_plant_in_another_workspace_is_not_readable(self):
        response = self.client.get('/plantings/specificplants/0/timeline/')
        self.assertEqual(response.status_code, 404)


class TimelineRowTests(PlantTimelineFixture):
    """The shared row shape is what both readers serialise."""

    def test_rows_carry_a_stable_identity_and_the_source_they_came_from(self):
        plant = self.germinated_plant()
        rows = timeline_rows(plant)
        self.assertEqual(
            sorted(rows[0]),
            [
                'corrected', 'corrects', 'detail', 'entry_id', 'kind', 'label',
                'occurred_at', 'source', 'source_id', 'summary',
            ],
        )
        self.assertEqual(rows[0]['entry_id'], f'lifecycle:germinated:{rows[0]["source_id"]}')
        self.assertEqual(rows[0]['label'], 'Germinated')

    def test_a_workspace_with_no_history_reads_as_an_empty_timeline(self):
        plant = make_specific_plant()
        self.assertEqual(plant_timeline(plant), [])
        self.assertEqual(
            NurseryObservation.objects.filter(targets__plant=plant).count(), 0,
        )
        self.assertEqual(
            PlantCohort.objects.filter(promoted_plants=plant).count(), 0,
        )
        self.assertIsNotNone(get_current_workspace())
