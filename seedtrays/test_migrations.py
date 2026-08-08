"""Tests for seed-tray data migrations.

The backfill is replayed over real rows rather than asserted from the migration
source, because what matters is the shape of the data an existing deployment
ends up with: which sowings are grouped, what is flagged, and — most of all —
what the migration refuses to guess.
"""
# pylint: disable=duplicate-code

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from applications.models import InputApplicationTarget
from plantings.models import SeedTrayPlanting
from tests.factories import (
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_planting,
)
from workspaces.models import Workspace

from .models import SeedTrayGeneration, SeedTrayGenerationEvent


def latest_seedtrays_state():
    """Return the newest migration state for the whole project.

    Resolved from the graph rather than pinned by name, so a later migration
    cannot leave the database half-migrated for the rest of the run, and every
    app's leaf is included because rewinding one app also unapplies the
    migrations of the apps that depend on it.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return list(executor.loader.graph.leaf_nodes())


class LegacyGenerationBackfillTests(TransactionTestCase):
    """Existing trays become usable without inventing what was not recorded."""

    UNLINKED_STATE = [('seedtrays', '0005_seedtraygeneration_seedtraygenerationevent_and_more')]

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def setUp(self):
        super().setUp()
        self.addCleanup(self._migrate, latest_seedtrays_state())

    @staticmethod
    def _migrate(targets):
        """Move the test database to one explicit migration state."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)

    @staticmethod
    def _unlink_generations():
        """Return the database to its pre-generation shape, keeping the sowings."""
        with connection.cursor() as cursor:
            cursor.execute('UPDATE plantings_seedtrayplanting SET generation_id = NULL')
            cursor.execute('DELETE FROM seedtrays_seedtraygenerationevent')
            cursor.execute('DELETE FROM seedtrays_seedtraygeneration')

    def _run_backfill(self):
        """Strip the generation links, then replay the backfill over them."""
        self._migrate(self.UNLINKED_STATE)
        self._unlink_generations()
        self._migrate(latest_seedtrays_state())

    def test_a_tray_with_sowings_gets_one_reviewable_generation(self):
        """Every existing sowing on a tray is grouped into one flagged fill."""
        tray = make_seed_tray()
        earlier = make_seed_tray_planting(seed_tray=tray)
        later = make_seed_tray_planting(seed_tray=tray)
        SeedTrayPlanting.objects.filter(pk=earlier.pk).update(
            planted='2026-03-01T08:00:00Z',
        )
        SeedTrayPlanting.objects.filter(pk=later.pk).update(
            planted='2026-04-01T08:00:00Z',
        )

        self._run_backfill()

        generation = SeedTrayGeneration.objects.get(tray=tray)
        self.assertEqual(generation.code, f'LEGACY-TRAY-{tray.pk}-1')
        self.assertEqual(generation.sequence, 1)
        self.assertEqual(generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(generation.origin, SeedTrayGeneration.Origin.LEGACY)
        self.assertEqual(
            generation.review_state,
            SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )
        self.assertEqual(generation.workspace_id, earlier.workspace_id)
        self.assertIsNone(generation.created_by)
        earlier.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(earlier.generation_id, generation.pk)
        self.assertEqual(later.generation_id, generation.pk)

    def test_the_fill_opens_when_its_earliest_sowing_was_recorded(self):
        """No date is invented; the first sowing is the earliest defensible one."""
        tray = make_seed_tray()
        first = make_seed_tray_planting(seed_tray=tray)
        make_seed_tray_planting(seed_tray=tray)
        SeedTrayPlanting.objects.filter(pk=first.pk).update(
            planted='2026-02-02T09:30:00Z',
        )

        self._run_backfill()

        generation = SeedTrayGeneration.objects.get(tray=tray)
        first.refresh_from_db()
        self.assertEqual(generation.opened_at, first.planted)

    def test_the_review_note_says_what_an_operator_has_to_confirm(self):
        """A bare flag would not tell anybody which decision is outstanding."""
        tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=tray)

        self._run_backfill()

        generation = SeedTrayGeneration.objects.get(tray=tray)
        self.assertIn('grouped into one fill', generation.review_details)
        self.assertIn(f'tray #{tray.pk}', generation.review_details)

    def test_historical_media_is_never_attributed_to_the_new_fill(self):
        """An application recorded before generations keeps an unknown one."""
        tray = make_seed_tray()
        cell = make_seed_tray_cell(tray=tray)
        make_seed_tray_planting(seed_tray=tray)
        target = InputApplicationTarget.objects.filter(seed_tray_cell=cell)

        self._run_backfill()

        self.assertFalse(target.filter(seed_tray_generation__isnull=False).exists())

    def test_a_tray_with_no_sowings_gets_no_generation(self):
        """An unused tray has had no fill, and the migration does not claim one."""
        tray = make_seed_tray()

        self._run_backfill()

        self.assertFalse(SeedTrayGeneration.objects.filter(tray=tray).exists())

    def test_the_backfill_records_why_the_generation_exists(self):
        """The opening event names the migration rather than an operator."""
        tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=tray)

        self._run_backfill()

        event = SeedTrayGenerationEvent.objects.get(generation__tray=tray)
        self.assertEqual(event.event_type, SeedTrayGenerationEvent.EventType.OPENED)
        self.assertIn('before tray generations existed', event.reason)
        self.assertIsNone(event.created_by)

    def test_sowings_without_a_tray_are_left_alone(self):
        """A sowing that names no tray has no fill to belong to."""
        trayless = make_seed_tray_planting()
        SeedTrayPlanting.objects.filter(pk=trayless.pk).update(seed_tray=None)

        self._run_backfill()

        trayless.refresh_from_db()
        self.assertIsNone(trayless.generation_id)

    def test_replaying_the_backfill_changes_nothing(self):
        """Re-running a deployment migration must not open a second fill.

        The links are left in place this time, which is what a real re-run sees.
        A second generation here would be a live tray silently split in two.
        """
        tray = make_seed_tray()
        make_seed_tray_planting(seed_tray=tray)
        self._run_backfill()
        generation = SeedTrayGeneration.objects.get(tray=tray)

        self._migrate(self.UNLINKED_STATE)
        self._migrate(latest_seedtrays_state())

        self.assertEqual(SeedTrayGeneration.objects.filter(tray=tray).count(), 1)
        self.assertEqual(SeedTrayGeneration.objects.get(tray=tray).pk, generation.pk)
        self.assertEqual(
            SeedTrayGenerationEvent.objects.filter(generation=generation).count(),
            1,
        )
