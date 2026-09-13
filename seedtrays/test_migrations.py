"""Tests for seed-tray data migrations.

The backfill is replayed over real rows rather than asserted from the migration
source, because what matters is the shape of the data an existing deployment
ends up with: which sowings are grouped, what is flagged, and — most of all —
what the migration refuses to guess.

The plain backfill cases share one replay, because they only read what it
produced; see tests/migration_replay.py for why that is worth the trouble. The
cases that need a different migration target, or a rollback that refuses, still
replay per test.
"""
# pylint: disable=duplicate-code

from django.db import connection
from django.utils import timezone

from applications.models import InputApplicationTarget
from inventory.models import InventoryItem
from inventory.units import UnitCode
from plantings.models import SeedTrayPlanting
from tests.factories import (
    make_inventory_item,
    make_location,
    make_numbered_container,
    make_stock_lot,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_seed_tray_planting,
)
from tests.migration_replay import (
    MigrationReplayTestCase,
    SharedMigrationReplayTestCase,
    latest_migration_state,
    migrate_to,
)

from .models import SeedTrayGeneration, SeedTrayGenerationEvent


UNLINKED_STATE = [('seedtrays', '0005_seedtraygeneration_seedtraygenerationevent_and_more')]


def unlink_generations():
    """Return the database to its pre-generation shape, keeping the sowings."""
    with connection.cursor() as cursor:
        cursor.execute('UPDATE plantings_seedtrayplanting SET generation_id = NULL')
        cursor.execute('DELETE FROM seedtrays_seedtraygenerationevent')
        cursor.execute('DELETE FROM seedtrays_seedtraygeneration')


def run_backfill():
    """Strip the generation links, then replay the backfill over them."""
    migrate_to(UNLINKED_STATE)
    unlink_generations()
    migrate_to(latest_migration_state())


class LegacyGenerationBackfillTests(SharedMigrationReplayTestCase):
    """Existing trays become usable without inventing what was not recorded.

    Every tray below is its own case, so one replay over all of them says the
    same thing as one replay each: the backfill groups per tray, and a tray
    only ever sees its own sowings.
    """

    @classmethod
    def set_up_replay(cls):
        """Lay down one tray per case, then backfill the lot in one pass."""
        cls.grouped_tray = make_seed_tray()
        cls.grouped_earlier = make_seed_tray_planting(seed_tray=cls.grouped_tray)
        cls.grouped_later = make_seed_tray_planting(seed_tray=cls.grouped_tray)
        SeedTrayPlanting.objects.filter(pk=cls.grouped_earlier.pk).update(
            planted='2026-03-01T08:00:00Z',
        )
        SeedTrayPlanting.objects.filter(pk=cls.grouped_later.pk).update(
            planted='2026-04-01T08:00:00Z',
        )

        cls.opening_tray = make_seed_tray()
        cls.opening_first = make_seed_tray_planting(seed_tray=cls.opening_tray)
        make_seed_tray_planting(seed_tray=cls.opening_tray)
        SeedTrayPlanting.objects.filter(pk=cls.opening_first.pk).update(
            planted='2026-02-02T09:30:00Z',
        )

        cls.review_tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=cls.review_tray)

        cls.media_tray = make_seed_tray()
        cls.media_cell = make_seed_tray_cell(tray=cls.media_tray)
        make_seed_tray_planting(seed_tray=cls.media_tray)

        cls.unused_tray = make_seed_tray()

        cls.event_tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=cls.event_tray)

        cls.trayless_sowing = make_seed_tray_planting()
        SeedTrayPlanting.objects.filter(pk=cls.trayless_sowing.pk).update(seed_tray=None)

        run_backfill()

    def test_a_tray_with_sowings_gets_one_reviewable_generation(self):
        """Every existing sowing on a tray is grouped into one flagged fill."""
        generation = SeedTrayGeneration.objects.get(tray=self.grouped_tray)
        self.assertEqual(generation.code, f'LEGACY-TRAY-{self.grouped_tray.pk}-1')
        self.assertEqual(generation.sequence, 1)
        self.assertEqual(generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(generation.origin, SeedTrayGeneration.Origin.LEGACY)
        self.assertEqual(
            generation.review_state,
            SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )
        self.assertEqual(generation.workspace_id, self.grouped_earlier.workspace_id)
        self.assertEqual(generation.inventory_unit_id, self.grouped_tray.inventory_unit_id)
        self.assertIsNone(generation.created_by)
        earlier = SeedTrayPlanting.objects.get(pk=self.grouped_earlier.pk)
        later = SeedTrayPlanting.objects.get(pk=self.grouped_later.pk)
        self.assertEqual(earlier.generation_id, generation.pk)
        self.assertEqual(later.generation_id, generation.pk)

    def test_the_fill_opens_when_its_earliest_sowing_was_recorded(self):
        """No date is invented; the first sowing is the earliest defensible one."""
        generation = SeedTrayGeneration.objects.get(tray=self.opening_tray)
        first = SeedTrayPlanting.objects.get(pk=self.opening_first.pk)
        self.assertEqual(generation.opened_at, first.planted)

    def test_the_review_note_says_what_an_operator_has_to_confirm(self):
        """A bare flag would not tell anybody which decision is outstanding."""
        generation = SeedTrayGeneration.objects.get(tray=self.review_tray)
        self.assertIn('grouped into one fill', generation.review_details)
        self.assertIn(f'tray #{self.review_tray.pk}', generation.review_details)

    def test_historical_media_is_never_attributed_to_the_new_fill(self):
        """An application recorded before generations keeps an unknown one."""
        target = InputApplicationTarget.objects.filter(seed_tray_cell=self.media_cell)
        self.assertFalse(target.filter(seed_tray_generation__isnull=False).exists())

    def test_a_tray_with_no_sowings_gets_no_generation(self):
        """An unused tray has had no fill, and the migration does not claim one."""
        self.assertFalse(SeedTrayGeneration.objects.filter(tray=self.unused_tray).exists())

    def test_the_backfill_records_why_the_generation_exists(self):
        """The opening event names the migration rather than an operator."""
        event = SeedTrayGenerationEvent.objects.get(generation__tray=self.event_tray)
        self.assertEqual(event.event_type, SeedTrayGenerationEvent.EventType.OPENED)
        self.assertIn('before tray generations existed', event.reason)
        self.assertIsNone(event.created_by)

    def test_sowings_without_a_tray_are_left_alone(self):
        """A sowing that names no tray has no fill to belong to."""
        trayless = SeedTrayPlanting.objects.get(pk=self.trayless_sowing.pk)
        self.assertIsNone(trayless.generation_id)

    def test_each_tray_is_grouped_without_reaching_into_another(self):
        """One pass over many trays still opens exactly one fill per used tray.

        The cases above share a replay, so this is what rules out the backfill
        gathering sowings across trays: the used trays get one fill each, and
        the unused one gets none.
        """
        used = [self.grouped_tray, self.opening_tray, self.review_tray,
                self.media_tray, self.event_tray]
        for tray in used:
            with self.subTest(tray=tray.pk):
                self.assertEqual(SeedTrayGeneration.objects.filter(tray=tray).count(), 1)
        self.assertEqual(SeedTrayGeneration.objects.count(), len(used))


class GenerationMigrationTargetTests(MigrationReplayTestCase):
    """Cases that each need their own migration target, so each replays once."""

    def setUp(self):
        super().setUp()
        self.addCleanup(migrate_to, latest_migration_state())

    def test_replaying_the_backfill_changes_nothing(self):
        """Re-running a deployment migration must not open a second fill.

        The links are left in place this time, which is what a real re-run sees.
        A second generation here would be a live tray silently split in two.
        """
        tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=tray)
        run_backfill()
        generation = SeedTrayGeneration.objects.get(tray=tray)

        migrate_to(UNLINKED_STATE)
        migrate_to(latest_migration_state())

        self.assertEqual(SeedTrayGeneration.objects.filter(tray=tray).count(), 1)
        self.assertEqual(SeedTrayGeneration.objects.get(tray=tray).pk, generation.pk)
        self.assertEqual(
            SeedTrayGenerationEvent.objects.filter(generation=generation).count(),
            1,
        )

    def test_container_backfill_preserves_open_and_closed_fill_history(self):
        """Linking a physical container changes neither fill identity nor events."""
        first = make_seed_tray_generation()
        SeedTrayGeneration.objects.filter(pk=first.pk).update(
            status=SeedTrayGeneration.Status.CLOSED,
            closed_at=first.opened_at,
            close_reason='Cleaned before container fills.',
        )
        make_seed_tray_generation(tray=first.tray, sequence=2)
        fields = [field.attname for field in SeedTrayGeneration._meta.fields
                  if field.name != 'inventory_unit']
        before = list(SeedTrayGeneration.objects.order_by('pk').values(*fields))
        events = list(SeedTrayGenerationEvent.objects.order_by('pk').values())

        migrate_to([('seedtrays', '0007_retire_tray_models')])
        migrate_to(latest_migration_state())

        self.assertEqual(list(SeedTrayGeneration.objects.order_by('pk').values(*fields)), before)
        self.assertEqual(list(SeedTrayGenerationEvent.objects.order_by('pk').values()), events)
        self.assertEqual(first.tray.inventory_unit.container_fills.count(), 2)

    def test_target_migration_refuses_to_discard_nontray_history(self):
        """Rollback stops before removing the only identity of a counted fill."""
        tray_fill = make_seed_tray_generation()
        lot = make_stock_lot(item=make_inventory_item(
            category=InventoryItem.Category.POT_CONTAINER, base_unit=UnitCode.EACH,
        ))
        fill = SeedTrayGeneration.objects.create(
            workspace=lot.workspace, stock_lot=lot, source_location=make_location(),
            container_count=50, code='COUNTED-POTS', sequence=1, opened_at=tray_fill.opened_at,
        )
        with self.assertRaisesMessage(RuntimeError, 'while non-tray fills exist'):
            migrate_to([('seedtrays', '0008_generation_inventory_unit')])

        # The refusal leaves the database in the state 0009 built, so only the
        # columns of that state can be read back: the model class describes a
        # later schema than the one under test.
        self.assertEqual(
            SeedTrayGeneration.objects.filter(pk=fill.pk)
            .values_list('stock_lot_id', 'container_count').first(),
            (lot.pk, 50),
        )
        self.assertEqual(
            SeedTrayGeneration.objects.filter(pk=tray_fill.pk)
            .values_list('inventory_unit_id', flat=True).first(),
            tray_fill.inventory_unit_id,
        )

    def test_share_basis_migration_refuses_to_discard_frozen_shares(self):
        """Rollback stops before dropping denominators no later state can rebuild."""
        unit = make_numbered_container()
        fill = SeedTrayGeneration.objects.create(
            workspace=unit.workspace, inventory_unit=unit,
            code='POT-SHARES', sequence=1, opened_at=timezone.now(),
        )
        SeedTrayGeneration.objects.filter(pk=fill.pk).update(plant_share_count=3)

        with self.assertRaisesMessage(RuntimeError, 'while frozen plant shares exist'):
            migrate_to([('seedtrays', '0010_replace_tray_models')])

        fill.refresh_from_db()
        self.assertEqual(fill.plant_share_count, 3)
