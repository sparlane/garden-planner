"""Withdrawing a germination that was recorded but never happened.

The tray screen paginated its seedlings at a hundred rows while the germination
figure beside the sowing was counted in the database over all of them, so cells
past the cut read as empty and whole fills were entered a second and a third
time. Those seedlings cannot be deleted — the bulk-operation audit that created
them and the cost layers that name them are both immutable and both refer to
them — and they must not be culled either, because a cull says a plant came up
and then died, which is exactly the claim that is false.

So the correction is a fact of its own: the germination is struck out, the
plant stops counting as something that came up, and the cost it was holding
goes back to the cell. These tests drive it through the service, the API, the
figures it is supposed to move, and the ones it must leave alone.
"""
# pylint: disable=duplicate-code

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APITestCase

from tests.factories import (
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_generation,
    make_growth_stage,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .cohorts import observe_cohort, promote_cohort
from .growth import record_observation
from .movement import move_specific_plant
from .germination import (
    close_germination,
    germination_summaries,
    germination_summary,
    ungerminated_by_cell,
)
from .lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    derive_state,
    record_germination_event,
    record_lifecycle_event,
    reverse_lifecycle_event,
    with_lifecycle_state,
)
from .withdrawal import (
    WITHDRAWAL_KEEPS,
    withdraw_germination,
    withdraw_germinations,
    withdrawal_blocking_relations,
)
from .models import (
    CohortOperation,
    PlantCohort,
    PlantLifecycleEvent,
    SpecificPlant,
    SpecificPlantLocation,
)


LossCause = CohortOperation.LossCause


class WithdrawalTestCase(APITestCase):
    """One tray sowing of ten seeds over two cells, entered once too often."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='grower')
        self.client.force_authenticate(self.user)
        self.workspace = Workspace.objects.get(pk=1)
        self.tray = make_seed_tray()
        make_seed_tray_generation(tray=self.tray)
        self.cells = [
            make_seed_tray_cell(tray=self.tray, x_position=index)
            for index in range(2)
        ]
        self.sowing = make_seed_tray_planting(seed_tray=self.tray, quantity=10)
        self.allocations = [
            make_seed_tray_cell_planting(
                seed_tray_planting=self.sowing, cell=cell, quantity=5,
            )
            for cell in self.cells
        ]

    def germinate(self, allocation, count=1):
        """Record seedlings coming up in one cell, the way the screen does."""
        plants = []
        for _index in range(count):
            plant = make_specific_plant(cell_planting=allocation)
            make_specific_plant_location(specific_plant=plant)
            record_germination_event(plant, self.user)
            plants.append(plant)
        return plants

    def withdraw(self, plant, reason='Entered twice from the tray screen.'):
        """Withdraw one germination with the ordinary defaults."""
        return withdraw_germination(plant, self.user, reason)


class WithdrawingOneGerminationTests(WithdrawalTestCase):
    """What the correction says, and what it stops saying."""

    def test_the_sowing_stops_counting_a_withdrawn_seedling(self):
        """Three entered and one of them imagined is a rate of two in ten."""
        plants = self.germinate(self.allocations[0], 3)

        self.withdraw(plants[2])

        summary = germination_summary(self.sowing)
        self.assertEqual(summary['observed_count'], 2)
        self.assertEqual(summary['sown_quantity'], 10)
        self.assertEqual(summary['ungerminated'], 8)

    def test_the_batched_summary_agrees_with_the_single_one(self):
        """Two readings of one figure cannot disagree about what came up."""
        plants = self.germinate(self.allocations[0], 3)
        self.withdraw(plants[2])

        self.assertEqual(
            germination_summaries([self.sowing])[self.sowing.pk]['observed_count'],
            germination_summary(self.sowing)['observed_count'],
        )

    def test_the_plant_row_survives_the_withdrawal(self):
        """The audit and the cost ledger still name it, so it cannot be erased."""
        plant = self.germinate(self.allocations[0])[0]

        self.withdraw(plant)

        self.assertTrue(SpecificPlant.objects.filter(pk=plant.pk).exists())

    def test_the_germination_stays_in_the_history_beside_its_correction(self):
        """A correction appends the truth; it does not edit the record."""
        plant = self.germinate(self.allocations[0])[0]

        correction = self.withdraw(plant, 'Second entry for the same cell.')

        germination = PlantLifecycleEvent.objects.get(
            plant=plant, event_type=EventType.GERMINATED,
        )
        self.assertEqual(correction.event_type, EventType.CORRECTED)
        self.assertEqual(correction.reversal_of_id, germination.pk)
        self.assertEqual(correction.reason, 'Second entry for the same cell.')

    def test_a_withdrawn_plant_reads_as_never_observed(self):
        """It is not growing, and it is not a loss either."""
        plant = self.germinate(self.allocations[0])[0]

        self.withdraw(plant)

        summary = derive_state(list(plant.lifecycle_events.all()))
        self.assertEqual(summary.state, LifecycleState.WITHDRAWN)
        self.assertFalse(summary.sellable)
        self.assertEqual(summary.final_outcome, EventType.CORRECTED)

    def test_the_database_reads_the_state_the_replay_does(self):
        """`with_lifecycle_state` and `derive_state` share one vocabulary."""
        plant = self.germinate(self.allocations[0])[0]
        self.withdraw(plant)

        row = with_lifecycle_state(
            SpecificPlant.objects.filter(pk=plant.pk)
        ).get()
        replayed = derive_state(list(plant.lifecycle_events.all()))

        self.assertEqual(row.lifecycle_state, LifecycleState.WITHDRAWN)
        self.assertEqual(row.lifecycle_state, replayed.state)
        self.assertEqual(row.final_outcome, replayed.final_outcome)
        self.assertEqual(row.final_outcome_at, replayed.final_outcome_at)
        self.assertFalse(row.sellable)

    def test_the_seedling_stops_standing_in_its_cell(self):
        """A plant that never came up is not occupying a cell of the tray."""
        plant = self.germinate(self.allocations[0])[0]

        self.withdraw(plant)

        self.assertFalse(
            plant.locations.filter(ended__isnull=True).exists(),
        )

    def test_a_withdrawn_seedling_leaves_its_seed_ungerminated(self):
        """The seed it was credited with goes back to the cell's remainder."""
        plants = self.germinate(self.allocations[0], 2)
        self.withdraw(plants[1])
        close_germination(
            self.sowing, self.user,
            loss_cause=LossCause.FAILED, reason='Window has passed.',
        )

        self.assertEqual(
            ungerminated_by_cell(self.sowing)[self.cells[0].pk], 4,
        )


class WithdrawalIsNotALossTests(WithdrawalTestCase):
    """The line between a seedling that never was and one that died."""

    def test_a_failed_seedling_still_counts_as_having_come_up(self):
        """Withdrawal must not become a tidier way of recording a death."""
        plants = self.germinate(self.allocations[0], 2)
        record_lifecycle_event(
            plants[1], self.user, OutcomeRequest(EventType.FAILED),
        )

        self.assertEqual(germination_summary(self.sowing)['observed_count'], 2)

    def test_a_plant_that_has_been_worked_on_cannot_be_withdrawn(self):
        """Something else is recorded about it, so more than one fact is wrong."""
        plant = self.germinate(self.allocations[0])[0]
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.READY),
        )

        with self.assertRaisesMessage(ValidationError, 'worked on since it came up'):
            self.withdraw(plant)

    def test_a_moved_seedling_cannot_be_withdrawn(self):
        """Somebody picked it up and put it somewhere, so it was real.

        A move records no lifecycle event, so the events alone would not catch
        this one.
        """
        plant = self.germinate(self.allocations[0])[0]
        move_specific_plant(
            plant,
            {
                'location_type': SpecificPlantLocation.SEED_TRAY_CELL,
                'seed_tray_cell': self.cells[1],
                'started': timezone.now(),
            },
            self.user,
        )

        with self.assertRaisesMessage(ValidationError, 'has been moved since it came up'):
            self.withdraw(plant)

    def test_a_withdrawal_needs_a_reason(self):
        """Unexplained, it is indistinguishable from the mis-click it corrects."""
        plant = self.germinate(self.allocations[0])[0]

        with self.assertRaisesMessage(ValidationError, 'A reason is required.'):
            self.withdraw(plant, '   ')

    def test_a_germination_cannot_be_withdrawn_twice(self):
        """The second withdrawal would claim a fact that no longer stands."""
        plant = self.germinate(self.allocations[0])[0]
        self.withdraw(plant)

        with self.assertRaisesMessage(ValidationError, 'already been withdrawn'):
            self.withdraw(plant)

    def test_a_plant_with_no_recorded_germination_has_none_to_withdraw(self):
        """Garden quick-add records no germination fact, so there is nothing here."""
        plant = make_specific_plant(cell_planting=self.allocations[0])

        with self.assertRaisesMessage(ValidationError, 'No germination was ever recorded'):
            self.withdraw(plant)

    def test_the_generic_correction_still_refuses_a_germination(self):
        """It would leave a plant with no beginning; this module is the way."""
        plant = self.germinate(self.allocations[0])[0]
        germination = PlantLifecycleEvent.objects.get(
            plant=plant, event_type=EventType.GERMINATED,
        )

        with self.assertRaisesMessage(ValidationError, 'Withdraw the germination instead'):
            reverse_lifecycle_event(germination, self.user, 'Never happened.')


class WithdrawalEligibilityTests(WithdrawalTestCase):
    """What being attached to other records says about a plant."""

    def test_every_relation_not_deliberately_kept_blocks_a_withdrawal(self):
        """A relation added later must deny the correction until it is considered.

        The alternative is a list of blockers that a new way of using a plant
        can be added behind, which would let a withdrawal strand exactly the
        records it must not.
        """
        accessors = {
            relation.get_accessor_name()
            for relation in SpecificPlant._meta.related_objects
        }

        self.assertEqual(
            set(withdrawal_blocking_relations()),
            accessors - WITHDRAWAL_KEEPS,
        )

    def test_the_kept_relations_are_all_real_relations(self):
        """A typo in the allowlist would silently stop blocking something."""
        accessors = {
            relation.get_accessor_name()
            for relation in SpecificPlant._meta.related_objects
        }

        self.assertEqual(WITHDRAWAL_KEEPS - accessors, set())

    def test_a_plant_somebody_has_used_is_refused_by_name(self):
        """The operator is told what to resolve, not just that it failed."""
        plant = self.germinate(self.allocations[0])[0]
        record_observation(
            self.workspace, self.user,
            plant_ids=[plant.pk],
            stage=make_growth_stage(workspace=self.workspace),
        )

        with self.assertRaisesMessage(ValidationError, 'nursery_observation_targets'):
            self.withdraw(plant)


class WithdrawingAPromotedPlantTests(WithdrawalTestCase):
    """A plant promoted out of a cohort was counted, not seen to come up.

    Its germination is the cohort's observation carried onto an identity, and
    the promotion took the unit out of the cohort. Withdrawing it would deny a
    fact the cohort's history still asserts and leave that unit nowhere.
    """

    def promote(self, stage=None):
        """Observe a block of four, optionally staged, and promote one of it."""
        cohort, _operation = observe_cohort(
            self.workspace, self.user,
            batch=self.sowing.batch, source_sowing=self.sowing,
            quantity=4, idempotency_key=uuid4(),
        )
        if stage is not None:
            record_observation(
                self.workspace, self.user, cohort_id=cohort.pk, stage=stage,
            )
            cohort.refresh_from_db()
        plants, _operation = promote_cohort(
            self.workspace, self.user,
            cohort_id=cohort.pk, expected_revision=cohort.revision,
            quantity=1, idempotency_key=uuid4(),
            reason='This one needs its own sale label.',
        )
        return cohort, plants[0]

    def test_a_plant_promoted_from_a_bare_count_is_refused_by_its_cohort(self):
        """Nothing else would stop it: the promotion recorded no observation."""
        cohort, plant = self.promote()

        with self.assertRaisesMessage(
            ValidationError, f'promoted from cohort {cohort.pk}',
        ):
            self.withdraw(plant)

        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 3)
        self.assertEqual(
            derive_state(list(plant.lifecycle_events.all())).state,
            LifecycleState.GROWING,
        )

    def test_the_refusal_does_not_depend_on_what_the_promotion_recorded(self):
        """A staged cohort's observation would block too, but for the wrong reason."""
        cohort, plant = self.promote(
            stage=make_growth_stage(workspace=self.workspace),
        )
        self.assertTrue(plant.nursery_observation_targets.exists())

        with self.assertRaisesMessage(
            ValidationError, f'promoted from cohort {cohort.pk}',
        ):
            self.withdraw(plant)

    def test_a_selection_holding_a_promoted_plant_withdraws_nothing(self):
        """The tray seedlings beside it are still correctable on their own."""
        seedlings = self.germinate(self.allocations[0], 2)
        cohort, plant = self.promote()

        with self.assertRaises(ValidationError):
            withdraw_germinations(
                [seedling.pk for seedling in seedlings] + [plant.pk],
                self.user, 'The whole fill went in twice.',
            )
        self.assertEqual(germination_summary(self.sowing)['observed_count'], 2)

        withdraw_germinations(
            [seedling.pk for seedling in seedlings], self.user,
            'The whole fill went in twice.',
        )
        self.assertEqual(germination_summary(self.sowing)['observed_count'], 0)
        self.assertEqual(
            PlantCohort.objects.get(pk=cohort.pk).quantity, 3,
        )

    def test_the_route_names_the_cohort_in_the_plant_field(self):
        """The operator is pointed at the cohort rather than a generic refusal."""
        cohort, plant = self.promote()

        response = self.client.post(
            '/plantings/specificplants/withdraw-germination/',
            {'plants': [plant.pk], 'reason': 'Promoted one too many.'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(f'cohort {cohort.pk}', str(response.data['plant']))
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                plant=plant, event_type=EventType.CORRECTED,
            ).exists()
        )


class WithdrawalSelectionTests(WithdrawalTestCase):
    """A fill was entered twice, so a fill is what gets corrected."""

    def test_a_selection_is_withdrawn_together(self):
        """The duplicate entry was one action and its correction is one too."""
        plants = self.germinate(self.allocations[0], 2)
        plants += self.germinate(self.allocations[1], 2)

        withdraw_germinations(
            [plant.pk for plant in plants[:3]],
            self.user,
            'The whole fill went in twice.',
        )

        self.assertEqual(germination_summary(self.sowing)['observed_count'], 1)

    def test_one_ineligible_plant_withdraws_none_of_them(self):
        """Half a correction leaves a figure that is wrong in a new way."""
        plants = self.germinate(self.allocations[0], 3)
        record_lifecycle_event(
            plants[2], self.user, OutcomeRequest(EventType.READY),
        )

        with self.assertRaises(ValidationError):
            withdraw_germinations(
                [plant.pk for plant in plants],
                self.user,
                'The whole fill went in twice.',
            )

        self.assertEqual(germination_summary(self.sowing)['observed_count'], 3)

    def test_an_empty_selection_is_refused(self):
        """Nothing selected is a mistake, not a correction of nothing."""
        with self.assertRaisesMessage(ValidationError, 'Select at least one plant.'):
            withdraw_germinations([], self.user, 'Nothing here.')


class WithdrawalRESTTests(WithdrawalTestCase):
    """The screen's route into the correction."""

    url = '/plantings/specificplants/withdraw-germination/'

    def test_the_route_withdraws_a_selection(self):
        """One request corrects the fill the screen recorded twice."""
        plants = self.germinate(self.allocations[0], 3)

        response = self.client.post(
            self.url,
            {
                'plants': [plant.pk for plant in plants[1:]],
                'reason': 'Recorded again after the screen showed the cell empty.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(germination_summary(self.sowing)['observed_count'], 1)

    def test_the_route_requires_a_reason(self):
        """The API holds the same audit line the service does."""
        plant = self.germinate(self.allocations[0])[0]

        response = self.client.post(
            self.url, {'plants': [plant.pk], 'reason': ''}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', response.data)

    def test_a_domain_refusal_arrives_as_a_field_error(self):
        """An operator sees why, in the field it belongs to."""
        plant = self.germinate(self.allocations[0])[0]
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.READY),
        )

        response = self.client.post(
            self.url,
            {'plants': [plant.pk], 'reason': 'Entered twice.'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('plant', response.data)

    def test_the_tray_screen_stops_being_sent_a_withdrawn_seedling(self):
        """The grid and the sowing figure are counted off the same seedlings.

        This is the invariant the pagination bug broke from the other side: a
        cell drawing a seedling its sowing says never came up puts the two
        numbers back out of step.
        """
        plants = self.germinate(self.allocations[0], 3)
        withdraw_germinations(
            [plants[2].pk], self.user, 'Entered twice.',
        )

        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/specificplants/'
        )
        sowings = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {row['pk'] for row in response.data},
            {plants[0].pk, plants[1].pk},
        )
        self.assertEqual(
            sowings.data[0]['germination']['observed_count'],
            len(response.data),
        )
