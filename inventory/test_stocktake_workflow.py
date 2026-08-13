"""Model contracts for reviewed nursery stocktake evidence."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from locations.models import Location
from workspaces.models import get_current_workspace

from .ledger import MovementRequest, OpeningBalanceRequest, post_opening_balance, post_stock_movement
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
