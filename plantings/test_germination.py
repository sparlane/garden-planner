"""Declaring a sowing finished germinating, and what that decision means.

The point of the fact is the difference it makes to a count: three of ten up on
day seven and three that will never be four are the same number until somebody
says the sowing is done. These tests drive that through the real paths — the
service, the tray screen's API, and the bulk germination entry — because the
policy is only worth anything if it holds wherever a seedling is recorded.
"""
# pylint: disable=duplicate-code

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from tests.factories import (
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
)
from workspaces.models import Workspace

from .germination import (
    close_germination,
    current_closure,
    germination_summaries,
    germination_summary,
    is_closed,
    reopen_germination,
    ungerminated_by_cell,
)
from .lifecycle import EventType
from .models import (
    CohortOperation,
    PlantLifecycleEvent,
    SowingGerminationClosure,
    SpecificPlant,
)


LossCause = CohortOperation.LossCause


class GerminationClosureTestCase(APITestCase):
    """One tray sowing of ten seeds spread over two cells."""

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
        """Record seedlings coming up in one cell, without the REST path."""
        return [
            make_specific_plant(cell_planting=allocation)
            for _index in range(count)
        ]

    def close(self, **overrides):
        """Close this sowing's germination with the ordinary defaults."""
        values = {'loss_cause': LossCause.FAILED, 'reason': 'Window has passed.'}
        values.update(overrides)
        return close_germination(self.sowing, self.user, **values)


class ClosingASowingTests(GerminationClosureTestCase):
    """The fact that ends the ambiguity, and the figures it fixes."""

    def test_an_open_sowing_reports_its_rate_as_provisional(self):
        """A running count is a floor, and has to say so."""
        self.germinate(self.allocations[0], 3)
        summary = germination_summary(self.sowing)
        self.assertTrue(summary['provisional'])
        self.assertEqual(summary['observed_count'], 3)
        self.assertEqual(summary['sown_quantity'], 10)
        self.assertIsNone(summary['closed_at'])

    def test_closing_records_the_observed_count_and_the_remainder(self):
        """Seven seeds that produced nothing are seven, with a cause."""
        self.germinate(self.allocations[0], 2)
        self.germinate(self.allocations[1], 1)
        closure = self.close()
        self.assertEqual(closure.sown_quantity, 10)
        self.assertEqual(closure.observed_count, 3)
        self.assertEqual(closure.ungerminated, 7)
        self.assertEqual(closure.loss_cause, LossCause.FAILED)
        self.assertFalse(germination_summary(self.sowing)['provisional'])

    def test_a_closed_sowing_that_produced_nothing_loses_all_its_seed(self):
        """Nothing came up, so every seed placed is the remainder."""
        closure = self.close()
        self.assertEqual(closure.observed_count, 0)
        self.assertEqual(closure.ungerminated, 10)

    def test_a_cell_that_over_delivered_leaves_no_negative_remainder(self):
        """One multigerm cluster is three seedlings, not minus two seeds."""
        self.germinate(self.allocations[0], 12)
        closure = self.close(loss_cause='')
        self.assertEqual(closure.observed_count, 12)
        self.assertEqual(closure.ungerminated, 0)
        self.assertEqual(closure.loss_cause, '')

    def test_a_remainder_needs_a_cause(self):
        """Loss without a cause is the free-text history this ends."""
        with self.assertRaisesMessage(ValidationError, 'recorded cause'):
            self.close(loss_cause='')

    def test_a_sowing_cannot_be_closed_twice(self):
        """The second close would be a second answer to a settled question."""
        self.close()
        with self.assertRaisesMessage(ValidationError, 'already been declared'):
            self.close()

    def test_a_sowing_with_no_seed_in_any_cell_has_nothing_to_close(self):
        """Seed that reached no cell could never have come up."""
        bare = make_seed_tray_planting(seed_tray=self.tray, quantity=4)
        with self.assertRaisesMessage(ValidationError, 'no seed in any cell'):
            close_germination(bare, self.user, loss_cause=LossCause.FAILED)

    def test_the_remainder_is_counted_per_cell_for_costing(self):
        """Cost follows cells, so the remainder has to be readable per cell."""
        self.germinate(self.allocations[0], 5)
        self.germinate(self.allocations[1], 2)
        self.assertEqual(ungerminated_by_cell(self.sowing), {})
        self.close()
        self.assertEqual(
            ungerminated_by_cell(self.sowing),
            {self.cells[1].pk: 3},
        )

    def test_a_closure_cannot_be_deleted(self):
        """The decision stays on file even after it is withdrawn."""
        closure = self.close()
        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            closure.delete()


class LateGerminationTests(GerminationClosureTestCase):
    """The recorded policy: a late seedling is real, and needs a reason."""

    def test_a_late_seedling_is_rejected_without_a_reason(self):
        """It contradicts a stated judgement, so it has to say why."""
        self.close()
        response = self.client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.allocations[0].pk},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', response.data)

    def test_a_late_seedling_with_a_reason_is_recorded_on_the_plant(self):
        """The reason belongs with the plant's own history."""
        self.close()
        response = self.client.post(
            '/plantings/specificplants/',
            {
                'cell_planting': self.allocations[0].pk,
                'reason': 'Came up nine days after the window closed.',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        event = PlantLifecycleEvent.objects.get(
            plant_id=response.data['pk'], event_type=EventType.GERMINATED,
        )
        self.assertEqual(event.reason, 'Came up nine days after the window closed.')

    def test_editing_an_existing_plant_carries_no_germination_reason(self):
        """The reason describes a seedling coming up, not a later edit."""
        plant = self.germinate(self.allocations[0])[0]
        self.close()
        response = self.client.patch(
            f'/plantings/specificplants/{plant.pk}/',
            {'notes': 'Renamed after the close.'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        plant.refresh_from_db()
        self.assertEqual(plant.notes, 'Renamed after the close.')

    def test_an_open_sowing_asks_for_no_reason(self):
        """The policy costs nothing until somebody has closed something."""
        response = self.client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.allocations[0].pk},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_the_late_arrival_moves_the_current_figures_not_the_snapshot(self):
        """Both are true: what was decided, and what is true now."""
        self.germinate(self.allocations[0], 3)
        closure = self.close()
        self.client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.allocations[0].pk, 'reason': 'Late.'},
            format='json',
        )
        closure.refresh_from_db()
        summary = germination_summary(self.sowing)
        self.assertEqual(closure.observed_count, 3)
        self.assertEqual(closure.ungerminated, 7)
        self.assertEqual(summary['observed_count'], 4)
        self.assertEqual(summary['ungerminated'], 6)
        self.assertEqual(summary['late_germinations'], 1)
        self.assertFalse(summary['provisional'])

    def test_a_bulk_germination_against_a_closed_sowing_needs_a_reason(self):
        """Forty seedlings entered at once follow the same policy as one."""
        self.close()
        response = self.client.post(
            '/plantings/bulk-operations/',
            {
                'action': 'germinate',
                'atomicity': 'all_or_nothing',
                'idempotency_key': '11111111-1111-1111-1111-111111111111',
                'selection_source': {
                    'mode': 'cell_plantings',
                    'cell_plantings': [self.allocations[0].pk],
                },
                'action_payload': {
                    'germinations': [
                        {'cell_planting': self.allocations[0].pk, 'quantity': 2},
                    ],
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', str(response.data))
        self.assertEqual(SpecificPlant.objects.count(), 0)

    def test_a_bulk_germination_with_a_reason_is_accepted(self):
        """The reason reaches every plant the entry created."""
        self.close()
        response = self.client.post(
            '/plantings/bulk-operations/',
            {
                'action': 'germinate',
                'atomicity': 'all_or_nothing',
                'idempotency_key': '22222222-2222-2222-2222-222222222222',
                'reason': 'A second flush after the cold snap.',
                'selection_source': {
                    'mode': 'cell_plantings',
                    'cell_plantings': [self.allocations[0].pk],
                },
                'action_payload': {
                    'germinations': [
                        {'cell_planting': self.allocations[0].pk, 'quantity': 2},
                    ],
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        reasons = set(
            PlantLifecycleEvent.objects
            .filter(event_type=EventType.GERMINATED)
            .values_list('reason', flat=True)
        )
        self.assertEqual(reasons, {'A second flush after the cold snap.'})


class ReopeningTests(GerminationClosureTestCase):
    """Withdrawing a close that should never have been recorded."""

    def test_reopening_requires_a_reason(self):
        """Undoing an audited judgement is itself an audited act."""
        closure = self.close()
        with self.assertRaisesMessage(ValidationError, 'reason is required'):
            reopen_germination(closure, self.user, '')

    def test_reopening_returns_the_sowing_to_provisional(self):
        """The count can climb again, so it stops being a result."""
        closure = self.close()
        reopen_germination(closure, self.user, 'Closed the wrong tray.')
        self.assertFalse(is_closed(self.sowing))
        self.assertIsNone(current_closure(self.sowing))
        self.assertTrue(germination_summary(self.sowing)['provisional'])

    def test_the_withdrawn_decision_stays_on_file(self):
        """What somebody decided, and that they took it back, are both facts."""
        closure = self.close()
        reopen_germination(closure, self.user, 'Closed the wrong tray.')
        closure.refresh_from_db()
        self.assertIsNotNone(closure.reopened_at)
        self.assertEqual(closure.reopened_reason, 'Closed the wrong tray.')
        self.assertEqual(closure.reopened_by, self.user)

    def test_a_reopened_sowing_can_be_closed_again(self):
        """The partial unique index frees the sowing, rather than the row."""
        closure = self.close()
        reopen_germination(closure, self.user, 'Counted too early.')
        self.germinate(self.allocations[0], 4)
        second = self.close()
        self.assertEqual(second.observed_count, 4)
        self.assertEqual(
            SowingGerminationClosure.objects.filter(sowing=self.sowing).count(), 2,
        )

    def test_a_close_cannot_be_withdrawn_twice(self):
        """A second withdrawal would rewrite the first one's reason."""
        closure = self.close()
        reopen_germination(closure, self.user, 'Counted too early.')
        with self.assertRaisesMessage(ValidationError, 'already been withdrawn'):
            reopen_germination(closure, self.user, 'Again.')


class GerminationApiTests(GerminationClosureTestCase):
    """The tray screen's two actions, and the figures beside them."""

    def test_the_sowing_carries_its_germination_state(self):
        """A screen showing a count can always say whether it is final."""
        self.germinate(self.allocations[0], 2)
        response = self.client.get(f'/plantings/seedtray/{self.sowing.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['germination']['observed_count'], 2)
        self.assertEqual(response.data['germination']['rate'], '0.200000')
        self.assertTrue(response.data['germination']['provisional'])

    def test_closing_through_the_api_returns_the_settled_figures(self):
        """The operator sees the decision they just made."""
        self.germinate(self.allocations[0], 4)
        response = self.client.post(
            f'/plantings/seedtray/{self.sowing.pk}/close-germination/',
            {'loss_cause': LossCause.FAILED, 'reason': 'Window has passed.'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        germination = response.data['germination']
        self.assertFalse(germination['provisional'])
        self.assertEqual(germination['ungerminated'], 6)
        self.assertEqual(germination['loss_cause'], LossCause.FAILED)

    def test_closing_rejects_a_cause_outside_the_shared_vocabulary(self):
        """Loss cause is structured data on this side of the API too."""
        response = self.client.post(
            f'/plantings/seedtray/{self.sowing.pk}/close-germination/',
            {'loss_cause': 'unspecified'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('loss_cause', response.data)

    def test_reopening_through_the_api_needs_a_reason(self):
        """The audited act stays audited at the boundary."""
        self.close()
        response = self.client.post(
            f'/plantings/seedtray/{self.sowing.pk}/reopen-germination/',
            {'reason': ''},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reopening_an_open_sowing_is_rejected(self):
        """There is nothing to withdraw."""
        response = self.client.post(
            f'/plantings/seedtray/{self.sowing.pk}/reopen-germination/',
            {'reason': 'Nothing to undo.'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_the_bulk_summary_agrees_with_the_one_read_singly(self):
        """A list reads the same figures as a detail screen, in fewer queries."""
        self.germinate(self.allocations[0], 3)
        self.close()
        self.client.post(
            '/plantings/specificplants/',
            {'cell_planting': self.allocations[1].pk, 'reason': 'Late.'},
            format='json',
        )
        bulk = germination_summaries([self.sowing])[self.sowing.pk]
        self.assertEqual(bulk, germination_summary(self.sowing))
        self.assertEqual(bulk['late_germinations'], 1)
