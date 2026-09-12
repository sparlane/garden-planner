"""Counted cleans dispose only of media not already carried by departed plants."""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from applications.services import post_application
from costing.services import effective_allocations, reallocate_batch
from inventory.ledger import physical_balance, unpromised_bulk
from plantings.counted_fills import plant_counted_fill
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import make_specific_plant

from .container_fills import clean_empty_fill, clean_pot_fill, open_counted_fill, reopen_pot_fill
from .generations import CloseRequest, MediaDisposition, contents_digest
from .pot_media import pot_fill_cost_breakdown, pot_fill_remaining_media
from .test_pot_media import PotMediaMixin


class CountedFillCleanTests(PotMediaMixin, CountedStockTestCase):
    """Departed shares survive cleaning, recovery, and correction of the clean."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 3)
        post_application(self.draft(), self.user)

    def join(self):
        """Use one original pot without naming or numbering it."""
        plant = make_specific_plant()
        row, = plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk])
        return row

    def leave(self, row):
        """Post the same departure and costs that moving or an outcome would use."""
        with self.captureOnCommitCallbacks(execute=True):
            row.ended = timezone.now()
            row.save(update_fields=['ended'])

    def clean_remaining(self, disposition='waste'):
        """Dispose of exactly the quantities shown by the current clean report."""
        media = tuple(MediaDisposition(
            row['lot'].pk, row['base_quantity'], disposition, 'Clean remaining mix.',
            self.store if disposition == 'reclaimed' else None,
        ) for row in pot_fill_remaining_media(self.fill))
        return clean_pot_fill(self.workspace, self.user, self.fill, CloseRequest(reason='Clean unused pots.', media=media))

    def test_partial_fill_clean_reconciles_rounded_quantities_and_costs(self):
        """The first plant's rounded third is never disposed of a second time."""
        row = self.join()
        self.leave(row)
        layer, = effective_allocations(row.specific_plant.batch)
        remaining, = pot_fill_remaining_media(self.fill)
        self.assertEqual(layer.base_quantity + remaining['base_quantity'], 50)
        self.assertEqual(remaining['base_quantity'], Decimal('33.333333333'))
        self.clean_remaining('reclaimed')
        report = pot_fill_cost_breakdown(self.fill)
        self.assertEqual(report['departed_cost'], layer.amount)
        self.assertEqual(report['held_cost'], 0)
        self.assertEqual(report['recovered_cost'], Decimal('66.666666666'))
        self.assertEqual(report['rounding_difference'], Decimal('-0.000066666'))
        self.assertEqual(sum(report[key] for key in ('departed_cost', 'held_cost', 'production_loss', 'recovered_cost', 'rounding_difference')), report['applied_cost'])
        self.assertEqual(physical_balance(self.media, self.store), Decimal('183.333333333'))
        self.assertIsNone(reallocate_batch(row.specific_plant.batch, self.user, 'manual_recalculate'))

    def test_fully_departed_fill_needs_no_residual_and_reopens_without_pots(self):
        """All used pots may be numbered without preventing a clean correction."""
        for _ in range(3):
            self.leave(self.join())
        self.assertEqual(pot_fill_remaining_media(self.fill), [])
        self.number(self.pots, 50)
        clean_empty_fill(self.workspace, self.user, self.fill, reason='All plants gone.')
        self.assertFalse(self.fill.residuals.exists())
        reopen_pot_fill(self.workspace, self.user, self.fill, 'Wrong clean date.')
        self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['departed_cost'], 100)

    def test_correction_claims_only_unplanted_pots_and_preserves_old_departures(self):
        """Released pots need not return for the unused portion to be restored."""
        row = self.join()
        self.leave(row)
        self.number(self.pots, 48)
        for _ in range(2):
            self.clean_remaining('reclaimed')
            self.assertEqual(unpromised_bulk(self.pots, self.store), 2)
            reopen_pot_fill(self.workspace, self.user, self.fill, 'Wrong pots.')
            self.assertEqual(unpromised_bulk(self.pots, self.store), 0)
            self.assertEqual(physical_balance(self.media, self.store), 150)
            self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'], Decimal('66.6666'))
            self.assertIsNone(reallocate_batch(row.specific_plant.batch, self.user, 'manual_recalculate'))
        self.assertEqual(self.fill.residuals.count(), 2)

    def test_clean_refuses_occupied_stale_and_backdated_confirmations(self):
        """Contents and chronology must still match when stock locks are taken."""
        row = self.join()
        digest = contents_digest({'plants': [], 'seeds': [], 'media': pot_fill_remaining_media(self.fill)})
        with self.assertRaisesMessage(ValidationError, 'Move the plants'):
            self.clean_remaining()
        self.leave(row)
        for values in ({'digest': digest}, {'occurred_at': row.started}):
            with self.assertRaises(ValidationError):
                clean_pot_fill(self.workspace, self.user, self.fill, CloseRequest(reason='Clean', **values))
        self.assertFalse(self.fill.residuals.exists())

    def test_reused_remaining_pots_block_correction_before_media_is_reversed(self):
        """Correcting a clean cannot restore mix into pots already numbered away."""
        self.leave(self.join())
        self.clean_remaining('reclaimed')
        self.number(self.pots, 50)
        with self.assertRaisesMessage(ValidationError, 'not enough empty'):
            reopen_pot_fill(self.workspace, self.user, self.fill, 'Wrong pots.')
        self.assertEqual(physical_balance(self.media, self.store), Decimal('183.333333333'))
        self.assertFalse(self.fill.residuals.filter(pot_correction__isnull=False).exists())

    def test_clean_cannot_dispose_of_original_total_after_a_departure(self):
        """Returning the entire application would count the plant's media twice."""
        self.leave(self.join())
        with self.assertRaises(ValidationError):
            self.clean()
        self.clean_remaining()
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['production_loss'], Decimal('66.666666666'))

    def test_split_waste_and_reclaim_reconcile_at_report_precision(self):
        """Rounding each disposition must not leave a phantom held remainder."""
        self.leave(self.join())
        self.clean(media=(
            MediaDisposition(self.media.pk, '16.666666666', 'waste'),
            MediaDisposition(self.media.pk, '16.666666667', 'reclaimed', 'Reusable.', self.store),
        ))
        report = pot_fill_cost_breakdown(self.fill)
        self.assertEqual(report['held_cost'], 0)
        self.assertEqual(report['production_loss'], Decimal('33.333333332'))
        self.assertEqual(report['recovered_cost'], Decimal('33.333333334'))
        self.assertEqual(report['rounding_difference'], Decimal('-0.000066666'))

    def test_multiple_lines_reserve_each_lines_departure_rounding(self):
        """Aggregating a lot before taking shares would reclaim extra quanta."""
        post_application(self.draft(), self.user)
        self.leave(self.join())
        remaining, = pot_fill_remaining_media(self.fill)
        self.assertEqual(remaining['base_quantity'], Decimal('66.666666666'))
        self.clean_remaining()
        self.assertEqual(self.fill.residuals.get().base_quantity, Decimal('66.666666666'))

    def test_unpriced_remaining_media_stays_unknown(self):
        """A known remaining quantity does not imply a known monetary value."""
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        self.leave(self.join())
        self.clean_remaining()
        report = pot_fill_cost_breakdown(self.fill)
        self.assertIsNone(report['production_loss'])
        self.assertIsNone(report['rounding_difference'])
        self.assertIsNone(report['held_cost'])
