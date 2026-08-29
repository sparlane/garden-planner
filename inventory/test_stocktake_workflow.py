"""Model contracts for reviewed nursery stocktake evidence."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from locations.models import Location
from plantings.lifecycle import LifecycleState, plant_lifecycle_summary
from plantings.models import PlantCohort
from seeds.models import SeedPacket
from seeds.services import (
    ensure_packet_inventory_identity,
    packet_inventory_snapshot,
)
from tests.factories import (
    make_plant_at_location,
    make_plant_variety,
    make_seed_packet,
    make_seed_tray,
    make_specific_plant,
    make_supplier,
)
from workspaces.models import Workspace, get_current_workspace

from .ledger import (
    MovementRequest,
    OpeningBalanceRequest,
    UnitMovementRequest,
    physical_balance,
    post_opening_balance,
    post_stock_movement,
    post_unit_movement,
)
from .models import (
    InventoryItem,
    StockMovement,
    Stocktake,
    StocktakeAttachment,
    StocktakeCount,
    StocktakeTarget,
    StocktakeVariance,
)
from .stocktakes import resolve_identity_target, transition_stocktake
from .units import UnitCode


class StocktakeWorkflowModelTests(TestCase):
    """Counts and review facts retain evidence instead of overwriting it."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stock-counter')
        self.location = Location.objects.create(
            workspace=self.workspace,
            name='North bench',
            code='STOCK-NORTH',
            location_type=Location.LocationType.BENCH,
        )
        self.stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            status=Stocktake.Status.OPEN,
            counted_at=timezone.now(),
            created_by=self.user,
            scope={'location': self.location.pk},
        )
        self.target = StocktakeTarget.objects.create(
            stocktake=self.stocktake,
            target_type=StocktakeTarget.TargetType.COHORT,
            target_key='cohort:7',
            target_object_id=7,
            display='Cohort 7',
            expected_location=self.location,
            expected_quantity=Decimal('12'),
            expected_state='growing',
            expected_snapshot={'quantity': '12'},
            source_revision='revision-3',
        )

    def test_count_attempts_are_immutable_and_one_can_be_accepted(self):
        """A recount appends evidence while selection happens on the target."""
        count = StocktakeCount.objects.create(
            target=self.target,
            counted_quantity=Decimal('11'),
            observed_location=self.location,
            counter=self.user,
        )
        self.target.accepted_count = count
        self.target.count_status = StocktakeTarget.CountStatus.COUNTED
        self.target.save(update_fields=['accepted_count', 'count_status', 'updated'])

        count.notes = 'Changed later'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            count.save()
        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            count.delete()

    def test_variance_and_attachment_retain_review_evidence(self):
        """Variance details and externally hosted evidence remain connected."""
        variance = StocktakeVariance.objects.create(
            target=self.target,
            kind=StocktakeVariance.Kind.QUANTITY,
            expected={'quantity': '12'},
            observed={'quantity': '11'},
            source_changed=True,
            current_revision='revision-4',
        )
        attachment = StocktakeAttachment.objects.create(
            stocktake=self.stocktake,
            target=self.target,
            url='https://example.test/count.jpg',
            label='Count sheet',
            created_by=self.user,
        )

        self.assertEqual(variance.target, self.target)
        self.assertEqual(attachment.stocktake, self.stocktake)
        self.assertTrue(self.stocktake.blind)

    def test_two_person_requirement_defaults_off(self):
        """Existing shared workspaces do not acquire a surprise restriction."""
        self.assertFalse(self.workspace.stocktake_two_person_required)

    def test_status_transition_rejects_a_stale_instance(self):
        """A concurrent status change cannot be overwritten by a stale request."""
        stale_stocktake = Stocktake.objects.get(pk=self.stocktake.pk)
        Stocktake.objects.filter(pk=self.stocktake.pk).update(
            status=Stocktake.Status.PAUSED,
        )

        with self.assertRaisesMessage(ValidationError, 'transition is not available'):
            transition_stocktake(
                stale_stocktake,
                {Stocktake.Status.OPEN},
                Stocktake.Status.PAUSED,
            )

        self.stocktake.refresh_from_db()
        self.assertEqual(self.stocktake.status, Stocktake.Status.PAUSED)

    def test_scanned_identity_resolution_reuses_an_unexpected_target(self):
        """Scan orchestration creates one stable target outside the frozen scope."""
        identity = SimpleNamespace(
            target_content_type=SimpleNamespace(model='plantcohort'),
            target_object_id=19,
            target_snapshot={'display': 'Cohort 19'},
        )

        first = resolve_identity_target(self.stocktake, identity)
        second = resolve_identity_target(self.stocktake, identity)

        self.assertEqual(first, second)
        self.assertEqual(first.target_type, StocktakeTarget.TargetType.COHORT)
        self.assertEqual(first.display, 'Cohort 19')
        self.assertTrue(first.unexpected)


class StocktakeWorkflowRestTests(APITestCase):
    """Blind count sheets reveal expectations only when review begins."""

    url = '/inventory/stocktakes/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stocktake-api')
        self.reviewer = get_user_model().objects.create_user(username='stocktake-reviewer')
        self.client.force_authenticate(self.user)
        self.location = Location.objects.create(
            workspace=self.workspace, name='Stock room', code='STOCK-ROOM',
            location_type=Location.LocationType.STORAGE,
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace, name='Potting mix',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )
        self.lot, _movement = post_opening_balance(
            self.workspace, self.user,
            OpeningBalanceRequest(
                item=self.item, quantity=Decimal('100'),
                destination=self.location, acquisition_total=Decimal('20'),
                received_on=timezone.localdate(),
            ),
        )

    def _open(self):
        response = self.client.post(self.url, {
            'scope': {
                'location': self.location.pk,
                'target_types': ['lot'],
            },
            'blind': True,
            'notes': 'Monthly count',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_blind_count_review_and_approval_preserve_attempt(self):
        """Review reveals the snapshot and requires an explicit variance action."""
        opened = self._open()
        target = opened.data['targets'][0]
        self.assertIsNone(target['expected_quantity'])
        counted = self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {'target': target['pk'], 'counted_quantity': '95'},
            format='json',
        )
        self.assertEqual(counted.status_code, 201, counted.data)

        reviewed = self.client.post(
            f"{self.url}{opened.data['pk']}/begin-review/", {}, format='json',
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        target = reviewed.data['targets'][0]
        self.assertEqual(target['expected_quantity'], '100.000000000')
        variance = target['variances'][0]
        self.assertEqual(variance['kind'], StocktakeVariance.Kind.QUANTITY)

        resolved = self.client.post(
            f"{self.url}{opened.data['pk']}/resolve-variance/",
            {
                'variance': variance['pk'], 'action': 'adjust',
                'reason': 'Measured spill',
            },
            format='json',
        )
        self.assertEqual(resolved.status_code, 200, resolved.data)
        approved = self.client.post(
            f"{self.url}{opened.data['pk']}/approve/", {}, format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], Stocktake.Status.APPROVED)
        self.assertEqual(len(approved.data['targets'][0]['counts']), 1)
        posted = self.client.post(
            f"{self.url}{opened.data['pk']}/post/", {}, format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(posted.data['status'], Stocktake.Status.POSTED)
        reconciliation = posted.data['targets'][0]['reconciliations'][0]
        self.assertEqual(reconciliation['domain'], 'lot')
        self.assertEqual(
            posted.data['targets'][0]['variances'][0]['variance_value'],
            '1.0000',
        )
        self.assertEqual(
            StockMovement.objects.filter(lot=self.lot).count(), 2,
        )
        reversed_response = self.client.post(
            f"{self.url}{opened.data['pk']}/reverse/",
            {'reason': 'Counted the wrong shelf'}, format='json',
        )
        self.assertEqual(reversed_response.status_code, 200, reversed_response.data)
        self.assertEqual(reversed_response.data['status'], Stocktake.Status.REVERSED)
        self.assertEqual(
            len(reversed_response.data['targets'][0]['reconciliations']), 2,
        )

    def test_changed_source_requires_explicit_conflict_acceptance(self):
        """A movement after opening cannot be silently folded into approval."""
        opened = self._open()
        target = opened.data['targets'][0]
        self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {'target': target['pk'], 'counted_quantity': '90'}, format='json',
        )
        post_stock_movement(
            self.workspace, self.user,
            MovementRequest(
                lot=self.lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('1'), source=self.location,
            ),
        )
        reviewed = self.client.post(
            f"{self.url}{opened.data['pk']}/begin-review/", {}, format='json',
        )
        variance = reviewed.data['targets'][0]['variances'][0]
        self.assertTrue(variance['source_changed'])
        rejected = self.client.post(
            f"{self.url}{opened.data['pk']}/resolve-variance/",
            {
                'variance': variance['pk'], 'action': 'adjust',
                'reason': 'Reviewed movement',
            }, format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(
            f"{self.url}{opened.data['pk']}/resolve-variance/",
            {
                'variance': variance['pk'], 'action': 'adjust',
                'reason': 'Reviewed movement', 'accept_conflict': True,
            }, format='json',
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)

    def test_changed_source_can_refresh_and_require_a_recount(self):
        """Refreshing preserves the old attempt but adopts a new expected revision."""
        opened = self._open()
        target = opened.data['targets'][0]
        self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {'target': target['pk'], 'counted_quantity': '90'}, format='json',
        )
        post_stock_movement(
            self.workspace, self.user,
            MovementRequest(
                lot=self.lot, movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('1'), source=self.location,
            ),
        )
        self.client.post(
            f"{self.url}{opened.data['pk']}/begin-review/", {}, format='json',
        )
        refreshed = self.client.post(
            f"{self.url}{opened.data['pk']}/refresh-target/",
            {'target': target['pk'], 'reason': 'Use the movement-corrected balance'},
            format='json',
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertEqual(refreshed.data['status'], Stocktake.Status.OPEN)
        row = refreshed.data['targets'][0]
        self.assertEqual(row['count_status'], StocktakeTarget.CountStatus.RECOUNT)
        self.assertEqual(len(row['counts']), 1)

    def test_new_stock_entering_scope_is_added_before_review(self):
        """A frozen scope reports a newly entered identity instead of ignoring it."""
        opened = self._open()
        target = opened.data['targets'][0]
        self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {'target': target['pk'], 'counted_quantity': '100'}, format='json',
        )
        post_opening_balance(
            self.workspace, self.user,
            OpeningBalanceRequest(
                item=self.item, quantity=Decimal('5'), destination=self.location,
                acquisition_total=Decimal('1'), received_on=timezone.localdate(),
            ),
        )
        response = self.client.post(
            f"{self.url}{opened.data['pk']}/begin-review/", {}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], Stocktake.Status.OPEN)
        self.assertEqual(response.data['progress']['total'], 2)
        self.assertTrue(next(row for row in response.data['targets'] if row['unexpected'])['unexpected'])

    def test_two_person_rule_rejects_a_counter_as_reviewer(self):
        """Optional separation applies to identities even before task 58 roles."""
        self.workspace.stocktake_two_person_required = True
        self.workspace.save(update_fields=['stocktake_two_person_required', 'updated'])
        opened = self._open()
        target = opened.data['targets'][0]
        self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {'target': target['pk'], 'counted_quantity': '100'}, format='json',
        )
        self.client.post(
            f"{self.url}{opened.data['pk']}/begin-review/", {}, format='json',
        )
        rejected = self.client.post(
            f"{self.url}{opened.data['pk']}/approve/", {}, format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.client.force_authenticate(self.reviewer)
        approved = self.client.post(
            f"{self.url}{opened.data['pk']}/approve/", {}, format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)


class StocktakeStatusMachineTests(APITestCase):
    """A count is never stranded in a status nobody can act on.

    `Stocktake.Status.DRAFT` belongs to the older line-based stocktake, which
    `Stocktake.objects.create` opens and `post_stocktake` posts; the counting
    workflow here opens straight into `open`. Both routes end at the same
    posted and reversed statuses, so both are named in the table.
    """

    url = '/inventory/stocktakes/'

    TRANSITIONS = {
        Stocktake.Status.DRAFT: {Stocktake.Status.POSTED},
        Stocktake.Status.OPEN: {Stocktake.Status.PAUSED, Stocktake.Status.REVIEW},
        Stocktake.Status.PAUSED: {Stocktake.Status.OPEN, Stocktake.Status.REVIEW},
        Stocktake.Status.REVIEW: {Stocktake.Status.APPROVED},
        Stocktake.Status.APPROVED: {Stocktake.Status.POSTED},
        Stocktake.Status.POSTED: {Stocktake.Status.REVERSED},
        Stocktake.Status.REVERSED: set(),
    }

    # A reversed count is resolved: its corrections have been compensated and
    # a fresh count is what replaces it.
    RESOLVED = {Stocktake.Status.REVERSED}

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='stocktake-machine')
        self.client.force_authenticate(self.user)
        self.location = Location.objects.create(
            workspace=self.workspace, name='Machine room', code='MACHINE-ROOM',
            location_type=Location.LocationType.STORAGE,
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace, name='Machine mix',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )
        self.lot, _movement = post_opening_balance(
            self.workspace, self.user,
            OpeningBalanceRequest(
                item=self.item, quantity=Decimal('100'),
                destination=self.location, acquisition_total=Decimal('20'),
                received_on=timezone.localdate(),
            ),
        )

    def statuses_without_exits(self, transitions):
        """Return unresolved statuses no action can move a count on from."""
        return {
            status for status, targets in transitions.items()
            if status not in self.RESOLVED and not (targets - {status})
        }

    def test_no_unresolved_status_is_a_dead_end(self):
        """A count always has a next step until it is posted and settled."""
        self.assertEqual(self.statuses_without_exits(self.TRANSITIONS), set())

    def test_a_status_added_without_an_exit_is_reported(self):
        """The invariant catches the omission rather than assuming care."""
        stuck = {**self.TRANSITIONS, Stocktake.Status.APPROVED: set()}
        self.assertEqual(
            self.statuses_without_exits(stuck), {Stocktake.Status.APPROVED},
        )

    def test_every_declared_status_is_a_real_choice(self):
        """The table cannot describe a status the model does not have."""
        named = set(self.TRANSITIONS) | set().union(*self.TRANSITIONS.values())
        self.assertEqual(named, set(Stocktake.Status.values))

    def act(self, stocktake_id, action_name, **payload):
        """Attempt one workflow action and return its response."""
        return self.client.post(
            f'{self.url}{stocktake_id}/{action_name}/', payload, format='json',
        )

    def opened_and_counted(self, short_by=Decimal('5')):
        """Open a blind count and record an attempt short of the ledger.

        The count is taken relative to the current balance rather than from a
        fixed figure, so a test that stages several stocktakes in turn still
        produces a variance after an earlier one has posted its correction.
        """
        counted = physical_balance(self.lot, self.location) - short_by
        opened = self.client.post(self.url, {
            'scope': {'location': self.location.pk, 'target_types': ['lot']},
            'blind': True, 'notes': 'Machine count',
        }, format='json')
        self.assertEqual(opened.status_code, 201, opened.data)
        counted_response = self.client.post(
            f"{self.url}{opened.data['pk']}/count/",
            {
                'target': opened.data['targets'][0]['pk'],
                'counted_quantity': str(counted),
            },
            format='json',
        )
        self.assertEqual(counted_response.status_code, 201, counted_response.data)
        return opened.data['pk']

    def reviewed(self):
        """Take a counted stocktake through review with its variance resolved."""
        stocktake_id = self.opened_and_counted()
        review = self.act(stocktake_id, 'begin-review')
        self.assertEqual(review.status_code, 200, review.data)
        variance = review.data['targets'][0]['variances'][0]
        resolved = self.act(
            stocktake_id, 'resolve-variance',
            variance=variance['pk'], action='adjust', reason='Measured spill',
        )
        self.assertEqual(resolved.status_code, 200, resolved.data)
        return stocktake_id

    def test_one_walk_takes_every_declared_transition(self):
        """Pause, resume, review, approve, post, and reverse, in order."""
        stocktake_id = self.opened_and_counted()
        for action_name, expected in (
                ('pause', Stocktake.Status.PAUSED),
                ('resume', Stocktake.Status.OPEN),
                ('begin-review', Stocktake.Status.REVIEW)):
            response = self.act(stocktake_id, action_name)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['status'], expected)
        variance = response.data['targets'][0]['variances'][0]
        self.act(
            stocktake_id, 'resolve-variance',
            variance=variance['pk'], action='adjust', reason='Measured spill',
        )
        for action_name, expected in (
                ('approve', Stocktake.Status.APPROVED),
                ('post', Stocktake.Status.POSTED)):
            response = self.act(stocktake_id, action_name)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['status'], expected)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('95'))
        reversed_response = self.act(
            stocktake_id, 'reverse', reason='Counted the wrong shelf',
        )
        self.assertEqual(reversed_response.status_code, 200, reversed_response.data)
        self.assertEqual(reversed_response.data['status'], Stocktake.Status.REVERSED)
        # The compensating correction returns the ledger to where it started,
        # rather than deleting the adjustment the count posted.
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('100'))

    def test_a_paused_count_can_go_straight_to_review(self):
        """Review is reachable from either counting status."""
        stocktake_id = self.opened_and_counted()
        self.assertEqual(self.act(stocktake_id, 'pause').status_code, 200)
        review = self.act(stocktake_id, 'begin-review')
        self.assertEqual(review.status_code, 200, review.data)
        self.assertEqual(review.data['status'], Stocktake.Status.REVIEW)

    def test_a_status_refuses_the_actions_that_are_not_its_own(self):
        """Each stage is reached by its own transition, never skipped."""
        for stage, refused in (
                ('open', ('resume', 'approve', 'post', 'reverse')),
                ('review', ('pause', 'resume', 'begin-review', 'post', 'reverse')),
                ('approved', ('pause', 'begin-review', 'approve', 'reverse')),
                ('posted', ('pause', 'begin-review', 'approve', 'post'))):
            for action_name in refused:
                with self.subTest(stage=stage, action=action_name):
                    stocktake_id = self.staged(stage)
                    response = self.act(
                        stocktake_id, action_name, reason='Attempted out of turn',
                    )
                    self.assertEqual(response.status_code, 400, response.data)

    def staged(self, stage):
        """Return a stocktake standing at one stage of the workflow."""
        if stage == 'open':
            return self.opened_and_counted()
        stocktake_id = self.reviewed()
        if stage == 'review':
            return stocktake_id
        self.assertEqual(self.act(stocktake_id, 'approve').status_code, 200)
        if stage == 'approved':
            return stocktake_id
        self.assertEqual(self.act(stocktake_id, 'post').status_code, 200)
        return stocktake_id

    def test_a_posted_count_is_compensated_only_once(self):
        """A second reversal would double the correction it already made."""
        stocktake_id = self.staged('posted')
        first = self.act(stocktake_id, 'reverse', reason='Counted the wrong shelf')
        self.assertEqual(first.status_code, 200, first.data)
        second = self.act(stocktake_id, 'reverse', reason='And again')
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('100'))

    def test_a_reversal_states_why_the_count_was_wrong(self):
        """A compensating correction never enters the record unexplained."""
        stocktake_id = self.staged('posted')
        for reason in ('', '   '):
            with self.subTest(reason=repr(reason)):
                response = self.act(stocktake_id, 'reverse', reason=reason)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('95'))


class StocktakeDomainReconciliationTests(APITestCase):
    """Every counted domain posts its own correction and compensates it.

    Only the lot domain had a test. The other posters each reach into a
    different subledger — cohorts, plant lifecycle, serialized trays — and
    each has its own compensating path in `_reverse_link`, which is where a
    reversal that does not actually restore the source would hide.
    """

    url = '/inventory/stocktakes/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='stocktake-domains')
        self.client.force_authenticate(self.user)
        self.bench = Location.objects.create(
            workspace=self.workspace, name='Bench one', code='BENCH-ONE',
            location_type=Location.LocationType.GROWING,
        )
        self.other_bench = Location.objects.create(
            workspace=self.workspace, name='Bench two', code='BENCH-TWO',
            location_type=Location.LocationType.GROWING,
        )

    def open_over(self, target_type, location=None):
        """Open a blind count of one kind of stock standing in one place."""
        response = self.client.post(self.url, {
            'scope': {
                'location': (location or self.bench).pk,
                'target_types': [target_type],
            },
            'blind': True, 'notes': f'{target_type} count',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data['targets']), 1, response.data)
        return response.data['pk'], response.data['targets'][0]['pk']

    def count(self, stocktake_id, target_id, **values):
        """Record what the counter physically observed."""
        response = self.client.post(
            f'{self.url}{stocktake_id}/count/',
            {'target': target_id, **values}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def resolve_and_post(self, stocktake_id, action, reason, payload=None):
        """Review, correct, approve, and post one counted target."""
        review = self.client.post(
            f'{self.url}{stocktake_id}/begin-review/', {}, format='json',
        )
        self.assertEqual(review.status_code, 200, review.data)
        variances = review.data['targets'][0]['variances']
        self.assertTrue(variances, review.data)
        for variance in variances:
            resolved = self.client.post(
                f'{self.url}{stocktake_id}/resolve-variance/',
                {
                    'variance': variance['pk'], 'action': action,
                    'reason': reason, 'payload': payload or {},
                },
                format='json',
            )
            self.assertEqual(resolved.status_code, 200, resolved.data)
        approved = self.client.post(
            f'{self.url}{stocktake_id}/approve/', {}, format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        posted = self.client.post(f'{self.url}{stocktake_id}/post/', {}, format='json')
        self.assertEqual(posted.status_code, 200, posted.data)
        return posted.data

    def reverse(self, stocktake_id, reason='Counted the wrong bench'):
        """Compensate every correction the count posted."""
        response = self.client.post(
            f'{self.url}{stocktake_id}/reverse/', {'reason': reason}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], Stocktake.Status.REVERSED)
        return response.data

    def test_a_short_cohort_count_is_posted_and_compensated(self):
        """The cohort is written down to what was counted, then restored."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch,
            quantity=10, location=self.bench,
        )
        stocktake_id, target_id = self.open_over('cohort')
        self.count(stocktake_id, target_id, counted_quantity='8')
        self.resolve_and_post(stocktake_id, 'adjust', 'Two plants missing')
        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 8)

        self.reverse(stocktake_id)

        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 10)

    def test_a_cohort_found_on_another_bench_is_moved_and_moved_back(self):
        """A misplaced cohort is relocated by the count, not rewritten."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch,
            quantity=10, location=self.bench,
        )
        stocktake_id, target_id = self.open_over('cohort')
        self.count(
            stocktake_id, target_id,
            counted_quantity='10', observed_location=self.other_bench.pk,
        )
        self.resolve_and_post(
            stocktake_id, 'move', 'Found on bench two',
            payload={'location': self.other_bench.pk},
        )
        cohort.refresh_from_db()
        self.assertEqual(cohort.location_id, self.other_bench.pk)

        self.reverse(stocktake_id)

        cohort.refresh_from_db()
        self.assertEqual(cohort.location_id, self.bench.pk)

    def bench_plant(self):
        """Stand one individually tracked plant on bench one."""
        return make_plant_at_location(self.bench, workspace=self.workspace)

    def test_a_plant_nobody_could_find_is_recorded_lost_and_restored(self):
        """A missing plant becomes a lifecycle fact the reversal takes back."""
        plant = self.bench_plant()
        stocktake_id, _target_id = self.open_over('plant')
        posted = self.resolve_and_post(stocktake_id, 'lost', 'Not on the bench')
        self.assertEqual(posted['targets'][0]['reconciliations'][0]['domain'], 'plant')
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.LOST)

        self.reverse(stocktake_id)

        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.GROWING)
        current = plant.locations.filter(ended__isnull=True).first()
        self.assertIsNotNone(current)
        self.assertEqual(current.location_id, self.bench.pk)

    def test_a_plant_found_elsewhere_is_moved_and_moved_back(self):
        """A misplaced plant's history gains two moves, not an edited one."""
        plant = self.bench_plant()
        stocktake_id, target_id = self.open_over('plant')
        self.count(stocktake_id, target_id, observed_location=self.other_bench.pk)
        self.resolve_and_post(
            stocktake_id, 'move', 'Found on bench two',
            payload={'location': self.other_bench.pk},
        )
        self.assertEqual(
            plant.locations.filter(ended__isnull=True).get().location_id,
            self.other_bench.pk,
        )

        self.reverse(stocktake_id)

        self.assertEqual(
            plant.locations.filter(ended__isnull=True).get().location_id,
            self.bench.pk,
        )
        self.assertEqual(plant.locations.count(), 3)

    def bench_tray(self):
        """Stand one serialized tray on bench one."""
        tray = make_seed_tray(workspace=self.workspace)
        post_unit_movement(
            self.workspace, self.user,
            UnitMovementRequest(
                unit=tray.inventory_unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=self.bench,
                reason='Placed on the bench.',
            ),
        )
        tray.inventory_unit.refresh_from_db()
        return tray

    def test_a_tray_found_elsewhere_is_moved_and_moved_back(self):
        """The serialized ledger carries the correction and its compensation."""
        tray = self.bench_tray()
        unit = tray.inventory_unit
        stocktake_id, target_id = self.open_over('tray')
        self.count(stocktake_id, target_id, observed_location=self.other_bench.pk)
        self.resolve_and_post(
            stocktake_id, 'move', 'Found on bench two',
            payload={'location': self.other_bench.pk},
        )
        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, self.other_bench.pk)

        self.reverse(stocktake_id)

        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, self.bench.pk)
        self.assertEqual(
            unit.movements.filter(
                movement_type=StockMovement.MovementType.REVERSAL,
            ).count(),
            1,
        )

    def received_packet(self, quantity='20', price='5.0000'):
        """Receive one packet of a known size through the seed workflow."""
        catalog = self.client.post('/seeds/seeds/', {
            'supplier': make_supplier(workspace=self.workspace).pk,
            'plant_variety': make_plant_variety(workspace=self.workspace).pk,
            'base_unit': 'seed',
        }, format='json')
        self.assertEqual(catalog.status_code, 201, catalog.data)
        draft = self.client.post('/seeds/packet-receipts/', {
            'seeds': catalog.data['pk'],
            'quantity_certainty': 'exact',
            'quantity': quantity,
            'line_price': price,
            'received_date': '2026-08-02',
        }, format='json')
        self.assertEqual(draft.status_code, 201, draft.data)
        posted = self.client.post(
            f"/seeds/packet-receipts/{draft.data['pk']}/post/", {}, format='json',
        )
        self.assertEqual(posted.status_code, 201, posted.data)
        return SeedPacket.objects.get(pk=posted.data['pk'])

    def packet_inventory(self, packet):
        """Return what the seeds app currently says is in one packet."""
        packet.refresh_from_db()
        return packet_inventory_snapshot(packet)

    def test_a_short_packet_count_is_posted_and_compensated(self):
        """A packet counted short draws the difference out of its container."""
        packet = self.received_packet()
        stocktake_id, target_id = self.open_over(
            'seed_packet', packet.storage_location,
        )
        self.count(stocktake_id, target_id, counted_quantity='17')
        posted = self.resolve_and_post(
            stocktake_id, 'adjust', 'Three seeds spilled',
        )

        self.assertEqual(
            posted['targets'][0]['reconciliations'][0]['domain'], 'seed_packet',
        )
        counted = self.packet_inventory(packet)
        self.assertEqual(counted['remaining_quantity'], Decimal('17'))
        # Nothing was sown, so the shortfall says the packet never held
        # twenty rather than that three went missing after it was opened.
        self.assertEqual(counted['received_quantity'], Decimal('17'))
        self.assertEqual(
            physical_balance(packet.stock_lot, packet.storage_location),
            Decimal('17'),
        )

        self.reverse(stocktake_id)

        restored = self.packet_inventory(packet)
        self.assertEqual(restored['remaining_quantity'], Decimal('20'))
        self.assertEqual(restored['received_quantity'], Decimal('20'))

    def test_counting_an_unopened_packet_establishes_what_is_in_it(self):
        """The count is the first number the packet has ever had."""
        packet = ensure_packet_inventory_identity(
            make_seed_packet(workspace=self.workspace),
        )
        before = self.packet_inventory(packet)
        self.assertEqual(before['quantity_certainty'], 'unknown')
        self.assertIsNone(before['remaining_quantity'])

        stocktake_id, target_id = self.open_over(
            'seed_packet', packet.storage_location,
        )
        self.count(stocktake_id, target_id, counted_quantity='24')
        self.resolve_and_post(stocktake_id, 'adjust', 'Counted the contents')

        counted = self.packet_inventory(packet)
        self.assertEqual(counted['quantity_certainty'], 'exact')
        self.assertEqual(counted['remaining_quantity'], Decimal('24'))
        self.assertEqual(counted['received_quantity'], Decimal('24'))

        self.reverse(stocktake_id)

        restored = self.packet_inventory(packet)
        self.assertEqual(restored['quantity_certainty'], 'unknown')
        self.assertIsNone(restored['remaining_quantity'])

    def test_a_packet_counted_at_what_it_already_held_raises_no_variance(self):
        """Agreement is not a correction, and nothing should be posted for it."""
        packet = self.received_packet()
        stocktake_id, target_id = self.open_over(
            'seed_packet', packet.storage_location,
        )
        self.count(stocktake_id, target_id, counted_quantity='20')

        review = self.client.post(
            f'{self.url}{stocktake_id}/begin-review/', {}, format='json',
        )

        self.assertEqual(review.status_code, 200, review.data)
        self.assertEqual(review.data['targets'][0]['variances'], [])
