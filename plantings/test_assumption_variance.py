"""An assumption measured against the crop it actually sized.

Every test here is about one of the three decisions task 99 records: report
the variance rather than auto-tuning it, compare like with like, and never
publish an observed figure without the sample size behind it.
"""

# Test method names carry the contract, and the fixture is a mixin rather
# than a base test case so the two suites do not re-run each other's tests.
# pylint: disable=missing-function-docstring,too-many-instance-attributes
# pylint: disable=invalid-name,too-few-public-methods,too-many-arguments
# pylint: disable=too-many-positional-arguments

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tests.factories import (
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
    make_plant_variety,
    make_planning_assumption,
    make_planning_stage_assumption,
    make_production_batch,
)

from .assumption_variance import (
    assumption_variance_rows,
    revise_assumption,
    revision_draft,
)
from .cohorts import change_cohort, observe_cohort
from .germination import close_germination
from .growth import record_observation
from .lifecycle import OutcomeRequest, record_lifecycle_event
from .models import (
    CohortOperation,
    GrowthStage,
    NurseryPlanningAssumption,
    NurseryPlanningInputAssumption,
    PlantLifecycleEvent,
)


class AssumptionFixture:
    """One variety, one assumption, and a helper that sows a real batch."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='grower')
        self.variety = make_plant_variety()
        self.workspace = self.variety.workspace
        self.workspace.assumption_tolerance_percent = Decimal('10')
        self.workspace.assumption_minimum_samples = 1
        self.workspace.save(update_fields=[
            'assumption_tolerance_percent', 'assumption_minimum_samples',
        ])
        self.stage = GrowthStage.objects.create(
            workspace=self.workspace, code='germination', name='Germination',
            display_order=10,
        )
        self.finishing = GrowthStage.objects.create(
            workspace=self.workspace, code='finishing', name='Finishing',
            display_order=20,
        )
        self.assumption = self._assumption(date(2026, 1, 1))
        self.packet = make_seed_packet(seeds=self._seeds())

    def _seeds(self):
        from tests.factories import make_seeds  # pylint: disable=import-outside-toplevel
        return make_seeds(plant_variety=self.variety, workspace=self.workspace)

    def _assumption(self, effective_from, **overrides):
        values = {
            'germination_rate': Decimal('0.85'),
            'seeds_per_cluster': 1,
            'tray_density': 10,
            **overrides,
        }
        assumption = make_planning_assumption(
            variety=self.variety, effective_from=effective_from, **values,
        )
        make_planning_stage_assumption(assumption=assumption, stage=self.stage)
        make_planning_stage_assumption(
            assumption=assumption, stage=self.finishing, sequence=2,
            lead_days=20, loss_rate=Decimal('0.2'),
        )
        return assumption

    def _sow(self, *, planted, sown=10, observed=5, close=True, tray=None):
        """Sow one tray, germinate a stated number, and optionally close it."""
        batch = make_production_batch(
            workspace=self.workspace, variety=self.variety, actual_start=planted,
        )
        tray = tray or make_seed_tray(workspace=self.workspace)
        sowing = make_seed_tray_planting(
            workspace=self.workspace, seeds_used=self.packet, batch=batch,
            seed_tray=tray, quantity=sown, planted=planted,
        )
        allocation = make_seed_tray_cell_planting(
            seed_tray_planting=sowing,
            cell=make_seed_tray_cell(tray=tray),
            quantity=sown,
        )
        plants = [
            make_specific_plant(workspace=self.workspace, cell_planting=allocation)
            for _index in range(observed)
        ]
        if close:
            close_germination(
                sowing, self.user, loss_cause=CohortOperation.LossCause.FAILED,
                reason='The window has passed.',
            )
        return batch, sowing, plants

    def _row(self, assumption=None):
        rows = assumption_variance_rows(
            self.workspace, assumption=(assumption or self.assumption).pk,
        )
        return rows[0]

    def _item(self):
        from tests.factories import make_inventory_item  # pylint: disable=import-outside-toplevel
        return make_inventory_item(workspace=self.workspace)


class AssumptionVarianceTests(AssumptionFixture, TestCase):
    """A rate assumed at 0.85 against sowings that really came up at 0.5."""

    def test_a_closed_sowing_reports_both_figures_and_the_sample_size(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=5)

        row = self._row()

        self.assertEqual(row['assumed_germination_rate'], '0.850000')
        self.assertEqual(row['observed_germination_rate'], '0.500000')
        self.assertEqual(row['germination_variance'], '-0.350000')
        self.assertEqual(row['batches'], 1)
        self.assertEqual(row['germination_sowings'], 1)
        self.assertEqual(row['germination_sown'], 10)
        self.assertEqual(row['germination_observed'], 5)
        self.assertTrue(row['germination_diverged'])
        self.assertEqual(row['divergences'], ['germination_rate'])

    def test_an_open_sowing_is_excluded_and_counted_rather_than_averaged_in(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=5, close=False)

        row = self._row()

        self.assertIsNone(row['observed_germination_rate'])
        self.assertEqual(row['germination_sowings'], 0)
        self.assertEqual(row['germination_open_sowings'], 1)
        self.assertFalse(row['diverged'])

    def test_batches_are_attributed_to_the_version_in_force_when_they_were_sown(self):
        later = self._assumption(date(2026, 6, 1), germination_rate=Decimal('0.5'))
        self.assumption.effective_until = date(2026, 5, 31)
        self.assumption.save(update_fields=['effective_until'])
        self._sow(planted=timezone.make_aware(timezone.datetime(2026, 3, 1, 9, 0)))
        self._sow(planted=timezone.make_aware(timezone.datetime(2026, 7, 1, 9, 0)))

        self.assertEqual(self._row()['batches'], 1)
        self.assertEqual(self._row(later)['batches'], 1)
        self.assertEqual(self._row(later)['effective_from'], date(2026, 6, 1))

    def test_a_batch_sown_before_any_version_took_effect_is_attributed_to_none(self):
        self._sow(planted=timezone.make_aware(timezone.datetime(2025, 11, 1, 9, 0)))

        row = self._row()

        self.assertEqual(row['batches'], 0)
        self.assertIsNone(row['observed_germination_rate'])

    def test_the_flag_respects_the_configured_tolerance(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=8)

        self.assertFalse(self._row()['germination_diverged'])

        self.workspace.assumption_tolerance_percent = Decimal('5')
        self.workspace.save(update_fields=['assumption_tolerance_percent'])

        self.assertTrue(self._row()['germination_diverged'])

    def test_a_thin_sample_reports_the_variance_but_raises_no_flag(self):
        self.workspace.assumption_minimum_samples = 5
        self.workspace.save(update_fields=['assumption_minimum_samples'])
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=5)

        row = self._row()

        self.assertEqual(row['observed_germination_rate'], '0.500000')
        self.assertEqual(row['batches'], 1)
        self.assertFalse(row['sample_sufficient'])
        self.assertFalse(row['diverged'])

    def test_tray_density_counts_only_the_fills_this_variety_had_to_itself(self):
        tray = make_seed_tray(workspace=self.workspace)
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=30, observed=0, tray=tray)
        shared = make_seed_tray(workspace=self.workspace)
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 2, 9, 0)), sown=20, observed=0, tray=shared)
        other = make_plant_variety(workspace=self.workspace)
        make_seed_tray_planting(
            workspace=self.workspace, seed_tray=shared, quantity=10,
            batch=make_production_batch(workspace=self.workspace, variety=other),
            seeds_used=make_seed_packet(),
            planted=timezone.make_aware(timezone.datetime(2026, 2, 2, 9, 0)),
        )

        row = self._row()

        self.assertEqual(row['observed_tray_density'], '30.00')
        self.assertEqual(row['tray_fills'], 1)
        self.assertEqual(row['tray_fills_shared'], 1)

    def test_stage_duration_counts_only_intervals_a_later_observation_closed(self):
        _batch, _sowing, plants = self._sow(
            planted=timezone.make_aware(timezone.datetime(2026, 2, 1, 9, 0)),
            sown=4, observed=2,
        )
        start = timezone.make_aware(timezone.datetime(2026, 2, 2, 9, 0))
        record_observation(
            self.workspace, self.user, plant_ids=[plants[0].pk],
            stage=self.stage, occurred_at=start,
        )
        record_observation(
            self.workspace, self.user, plant_ids=[plants[0].pk],
            stage=self.finishing, occurred_at=start + timedelta(days=14),
        )
        record_observation(
            self.workspace, self.user, plant_ids=[plants[1].pk],
            stage=self.stage, occurred_at=start,
        )

        stages = {row['stage_id']: row for row in self._row()['stages']}

        self.assertEqual(stages[self.stage.pk]['observed_lead_days'], '14.00')
        self.assertEqual(stages[self.stage.pk]['lead_days_samples'], 1)
        self.assertIsNone(stages[self.finishing.pk]['observed_lead_days'])
        self.assertEqual(stages[self.finishing.pk]['lead_days_samples'], 0)

    def test_a_loss_is_charged_to_the_stage_the_plant_was_standing_in(self):
        _batch, _sowing, plants = self._sow(
            planted=timezone.make_aware(timezone.datetime(2026, 2, 1, 9, 0)),
            sown=4, observed=4,
        )
        start = timezone.make_aware(timezone.datetime(2026, 2, 2, 9, 0))
        for plant in plants:
            record_observation(
                self.workspace, self.user, plant_ids=[plant.pk],
                stage=self.stage, occurred_at=start,
            )
        record_lifecycle_event(plants[0], self.user, OutcomeRequest(
            event_type=PlantLifecycleEvent.EventType.FAILED,
            occurred_at=start + timedelta(days=1),
            reason='Damped off.',
        ))

        stages = {row['stage_id']: row for row in self._row()['stages']}

        self.assertEqual(stages[self.stage.pk]['entered_units'], 4)
        self.assertEqual(stages[self.stage.pk]['lost_units'], 1)
        self.assertEqual(stages[self.stage.pk]['observed_loss_rate'], '0.250000')
        self.assertEqual(self._row()['unstaged_losses'], 0)

    def test_a_loss_with_no_stage_observation_is_totalled_apart(self):
        _batch, _sowing, plants = self._sow(
            planted=timezone.make_aware(timezone.datetime(2026, 2, 1, 9, 0)),
            sown=4, observed=2,
        )
        record_lifecycle_event(plants[0], self.user, OutcomeRequest(
            event_type=PlantLifecycleEvent.EventType.FAILED,
            occurred_at=timezone.make_aware(timezone.datetime(2026, 2, 3, 9, 0)),
            reason='Damped off.',
        ))

        row = self._row()

        self.assertEqual(row['unstaged_losses'], 1)
        self.assertEqual(
            sum(stage['lost_units'] for stage in row['stages']), 0,
        )

    def test_anonymous_stock_enters_a_stage_by_the_count_it_was_holding(self):
        batch, sowing, _plants = self._sow(
            planted=timezone.make_aware(timezone.datetime(2026, 2, 1, 9, 0)),
            sown=10, observed=0,
        )
        cohort, _operation = observe_cohort(
            self.workspace, self.user, batch=batch, quantity=8,
            source_sowing=sowing, idempotency_key=uuid4(),
        )
        start = timezone.make_aware(timezone.datetime(2026, 2, 3, 9, 0))
        record_observation(
            self.workspace, self.user, cohort_id=cohort.pk,
            stage=self.stage, occurred_at=start,
        )
        change_cohort(
            self.workspace, self.user, cohort_id=cohort.pk,
            expected_revision=cohort.revision, action=CohortOperation.Action.LOSS,
            loss_cause=CohortOperation.LossCause.FAILED, quantity=2,
            reason='Counted off the bench.', idempotency_key=uuid4(),
            occurred_at=start + timedelta(days=1),
        )

        stages = {row['stage_id']: row for row in self._row()['stages']}

        self.assertEqual(stages[self.stage.pk]['entered_units'], 8)
        self.assertEqual(stages[self.stage.pk]['lost_units'], 2)
        self.assertEqual(stages[self.stage.pk]['observed_loss_rate'], '0.250000')


class AssumptionRevisionTests(AssumptionFixture, TestCase):
    """Accepting a revision writes a new version and leaves history intact."""

    def test_the_draft_pre_fills_the_observed_value_and_names_its_source(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=5)

        draft = revision_draft(self.assumption)

        self.assertEqual(draft['germination_rate'], '0.500000')
        self.assertEqual(draft['germination_rate_source'], 'observed')
        self.assertEqual(draft['tray_density'], 10)
        self.assertEqual(draft['tray_density_source'], 'observed')
        self.assertGreater(draft['effective_from'], self.assumption.effective_from)
        self.assertEqual(
            NurseryPlanningAssumption.objects.filter(workspace=self.workspace).count(), 1,
        )

    def test_the_draft_keeps_the_standing_judgement_where_nothing_was_observed(self):
        draft = revision_draft(self.assumption)

        self.assertEqual(draft['germination_rate'], '0.850000')
        self.assertEqual(draft['germination_rate_source'], 'assumed')
        self.assertEqual(
            [stage['lead_days'] for stage in draft['stages']], [10, 20],
        )
        self.assertEqual(
            [stage['lead_days_source'] for stage in draft['stages']],
            ['assumed', 'assumed'],
        )

    def test_the_draft_caps_a_multigerm_rate_the_field_cannot_hold(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=4, observed=8)

        draft = revision_draft(self.assumption)

        self.assertEqual(self._row()['observed_germination_rate'], '2.000000')
        self.assertEqual(draft['germination_rate'], '1.000000')
        self.assertTrue(draft['germination_rate_capped'])

    def test_a_rate_of_nought_keeps_the_standing_judgement(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=0)

        draft = revision_draft(self.assumption)

        self.assertEqual(self._row()['observed_germination_rate'], '0.000000')
        self.assertEqual(draft['germination_rate'], '0.850000')
        self.assertEqual(draft['germination_rate_source'], 'assumed')
        self.assertFalse(draft['germination_rate_capped'])

    def test_a_revision_takes_over_the_window_the_old_version_held(self):
        self.assumption.effective_until = date(2026, 5, 31)
        self.assumption.save(update_fields=['effective_until'])

        revision = revise_assumption(self.assumption, effective_from=date(2026, 3, 1))

        self.assumption.refresh_from_db()
        self.assertEqual(self.assumption.effective_until, date(2026, 2, 28))
        self.assertEqual(revision.effective_until, date(2026, 5, 31))

    def test_a_revision_after_the_old_window_closed_is_open_ended(self):
        self.assumption.effective_until = date(2026, 5, 31)
        self.assumption.save(update_fields=['effective_until'])

        revision = revise_assumption(self.assumption, effective_from=date(2026, 7, 1))

        self.assumption.refresh_from_db()
        self.assertEqual(self.assumption.effective_until, date(2026, 5, 31))
        self.assertIsNone(revision.effective_until)

    def test_accepting_a_revision_closes_the_old_version_and_copies_its_detail(self):
        NurseryPlanningInputAssumption.objects.create(
            assumption=self.assumption,
            item=self._item(), quantity_per_plant=Decimal('0.25'),
        )

        revision = revise_assumption(
            self.assumption, effective_from=date(2026, 7, 1),
            germination_rate=Decimal('0.5'),
            stages=[{'stage': self.stage.pk, 'lead_days': 14}],
        )

        self.assumption.refresh_from_db()
        self.assertEqual(self.assumption.effective_until, date(2026, 6, 30))
        self.assertEqual(self.assumption.germination_rate, Decimal('0.85'))
        self.assertEqual(revision.germination_rate, Decimal('0.500000'))
        self.assertEqual(revision.tray_density, self.assumption.tray_density)
        self.assertEqual(
            list(revision.stages.order_by('sequence').values_list(
                'stage_id', 'lead_days', 'loss_rate',
            )),
            [
                (self.stage.pk, 14, Decimal('0.100000')),
                (self.finishing.pk, 20, Decimal('0.200000')),
            ],
        )
        self.assertEqual(revision.inputs.count(), 1)

    def test_a_revision_cannot_start_before_the_version_it_replaces(self):
        with self.assertRaises(ValidationError) as caught:
            revise_assumption(self.assumption, effective_from=date(2025, 12, 1))

        self.assertIn('effective_from', caught.exception.message_dict)

    def test_a_revision_refuses_a_stage_that_is_not_on_the_assumption(self):
        other = GrowthStage.objects.create(
            workspace=self.workspace, code='hardening', name='Hardening',
            display_order=30,
        )

        with self.assertRaises(ValidationError) as caught:
            revise_assumption(
                self.assumption, effective_from=date(2026, 7, 1),
                stages=[{'stage': other.pk, 'lead_days': 3}],
            )

        self.assertIn('stages', caught.exception.message_dict)

    def test_the_replaced_version_stops_asking_to_be_reviewed(self):
        self._sow(planted=timezone.make_aware(
            timezone.datetime(2026, 2, 1, 9, 0)), sown=10, observed=5)
        self.assertIsNone(self._row()['superseded_by'])

        revision = revise_assumption(self.assumption, effective_from=date(2026, 7, 1))

        self.assertEqual(self._row()['superseded_by'], revision.pk)
