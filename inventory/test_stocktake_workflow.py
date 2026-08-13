"""Model contracts for reviewed nursery stocktake evidence."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from workspaces.models import get_current_workspace

from .models import (
    Stocktake,
    StocktakeAttachment,
    StocktakeCount,
    StocktakeTarget,
    StocktakeVariance,
)


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
