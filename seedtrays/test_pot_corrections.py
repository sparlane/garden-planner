"""Mistaken pot cleans restore stock claims and media without erasing history."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from applications.services import post_application, reverse_application
from inventory.ledger import physical_balance, unpromised_bulk
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import make_location, make_specific_plant_location

from .container_fills import clean_empty_fill, open_counted_fill, open_numbered_fill, reopen_pot_fill
from .generations import MediaDisposition
from .pot_media import pot_fill_cost_breakdown, pot_fill_media_departures
from .test_pot_media import PotMediaMixin


class PotCleanCorrectionTests(PotMediaMixin, CountedStockTestCase):
    """Corrections remain atomic through stock reuse and repeated clean cycles."""

    def setUp(self):
        super().setUp()
        self.setup_media()

    def reopen(self, fill=None):
        """Correct the selected fill using an explicit audit reason."""
        return reopen_pot_fill(self.workspace, self.user, fill or self.fill, 'Cleaned the wrong pots.')

    def test_repeated_corrections_preserve_residuals_and_restore_cost(self):
        """Waste and reclaimed media retire together, once per mistaken clean."""
        application = self.draft()
        post_application(application, None)
        for _ in range(2):
            self.clean(media=(
                MediaDisposition(self.media.pk, '20', 'waste'),
                MediaDisposition(self.media.pk, '30', 'reclaimed', 'Reusable mix.', self.store),
            ))
            self.assertEqual(physical_balance(self.media, self.store), 180)
            self.reopen()
            self.assertEqual(physical_balance(self.media, self.store), 150)
            self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
            report = pot_fill_cost_breakdown(self.fill)
            self.assertEqual(report['held_cost'], 100)
            self.assertEqual(report['production_loss'], 0)
            self.assertEqual(report['recovered_cost'], 0)
        self.assertEqual(self.fill.residuals.count(), 4)
        self.assertEqual(self.fill.residuals.filter(pot_correction__isnull=False).count(), 4)
        self.assertEqual(self.fill.events.filter(event_type='closed').count(), 2)
        self.assertEqual(self.fill.events.filter(event_type='reopened', created_by=self.user).count(), 2)
        reverse_application(application, None, 'Wrong media application too.')
        self.assertEqual(physical_balance(self.media, self.store), 200)
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'], 0)

    def test_reused_counted_stock_refuses_reopening_without_partial_reversal(self):
        """A fill cannot take back pots already held by a later fill."""
        post_application(self.draft(), None)
        self.clean()
        open_counted_fill(self.workspace, None, self.pots, self.store, 100)
        with self.assertRaisesMessage(ValidationError, 'not enough empty'):
            self.reopen()
        self.assertFalse(self.fill.events.filter(event_type='reopened').exists())
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['production_loss'], 100)

    def test_consumed_recovery_rolls_back_every_correction(self):
        """Unavailable reclaimed mix leaves both waste and recovery effective."""
        post_application(self.draft(), None)
        destination = make_location()
        self.clean(media=(MediaDisposition(self.media.pk, '50', 'reclaimed', 'Reusable mix.', destination),))
        other = open_counted_fill(self.workspace, None, self.pots, self.store, 1)
        post_application(self.draft(other, source_location=destination), None)
        with self.assertRaises(ValidationError):
            self.reopen()
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, 'closed')
        self.assertFalse(self.fill.residuals.filter(pot_correction__isnull=False).exists())
        self.assertEqual(unpromised_bulk(self.pots, self.store), 99)

    def test_numbered_refill_prevents_restoring_earlier_fill_even_after_clean(self):
        """A correction cannot move an earlier fill across later physical reuse."""
        unit = self.number(self.pots, 1)[0]
        fill = open_numbered_fill(self.workspace, None, unit)
        clean_empty_fill(self.workspace, None, fill, reason='Clean')
        later = open_numbered_fill(self.workspace, None, unit)
        clean_empty_fill(self.workspace, None, later, reason='Clean again')
        with self.assertRaisesMessage(ValidationError, 'filled again'):
            self.reopen(fill)

    def test_used_numbered_fill_keeps_departure_shares_after_correction(self):
        """Reopening the clean never takes media back from departed plants."""
        unit = self.number(self.pots, 1)[0]
        fill = open_numbered_fill(self.workspace, None, unit)
        post_application(self.draft(fill), None)
        placement = make_specific_plant_location(location_type='container_unit', container_unit=unit, seed_tray_cell=None)
        placement.ended = timezone.now()
        placement.save()
        before = pot_fill_media_departures(fill)
        clean_empty_fill(self.workspace, None, fill, reason='Clean')
        reopened = self.reopen(fill)
        self.assertEqual(reopened.plant_share_count, 1)
        self.assertEqual(pot_fill_media_departures(fill), before)
        self.assertEqual(pot_fill_cost_breakdown(fill)['departed_cost'], 100)
        clean_empty_fill(self.workspace, None, reopened, reason='Actually clean now')

    def test_later_bare_pot_plant_history_prevents_reopening(self):
        """Later cultivation counts as reuse even if no new fill was opened."""
        unit = self.number(self.pots, 1)[0]
        fill = open_numbered_fill(self.workspace, None, unit)
        clean_empty_fill(self.workspace, None, fill, reason='Clean')
        placement = make_specific_plant_location(location_type='container_unit', container_unit=unit, seed_tray_cell=None)
        placement.ended = timezone.now()
        placement.save()
        with self.assertRaisesMessage(ValidationError, 'held plants since'):
            self.reopen(fill)

    def test_open_fill_blank_reason_and_foreign_workspace_are_refused(self):
        """Validation failures leave the original audit history untouched."""
        with self.assertRaisesMessage(ValidationError, 'Only a closed'):
            self.reopen()
        clean_empty_fill(self.workspace, None, self.fill, reason='Clean')
        with self.assertRaisesMessage(ValidationError, 'reason is required'):
            reopen_pot_fill(self.workspace, self.user, self.fill, ' ')
        with self.assertRaises(ValidationError):
            reopen_pot_fill(None, self.user, self.fill, 'Wrong workspace')
        self.assertEqual(self.fill.events.count(), 2)
