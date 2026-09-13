"""Base cases for the tests that replay real data migrations over real rows.

Rewinding one app unapplies every migration that depends on it, and by now that
is most of the project: returning ``seedtrays`` to 0005 unapplies 106 of the
199 migrations across fifteen apps, and ``plantings`` to 0019 unapplies 113.
One rewind-and-replay round trip therefore costs around two minutes, almost all
of it in Django rebuilding model state in Python rather than in the database,
so a class that pays it per test is the most expensive thing in the suite.

A class whose tests only *read* what one replay produced should subclass
:class:`SharedMigrationReplayTestCase` and build every fixture up front, so the
round trip is paid once for the class. A class whose tests each need a
different migration state — a different target, or a rollback expected to
refuse — has to keep paying per test, and subclasses
:class:`MigrationReplayTestCase`.
"""
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from workspaces.models import Workspace


def latest_migration_state():
    """Return the newest migration state for the whole project.

    Resolved from the graph rather than pinned by name, so a later migration
    cannot leave the database half-migrated for the rest of the run, and every
    app's leaf is included because rewinding one app also unapplies the
    migrations of the apps that depend on it.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return list(executor.loader.graph.leaf_nodes())


def migrate_to(targets):
    """Move the test database to one explicit migration state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def restore_seed_workspace():
    """Recreate the workspace row migrations seed and flushing removes."""
    if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
        Workspace.objects.create(
            pk=settings.CURRENT_WORKSPACE_ID,
            name='My Garden',
        )


class MigrationReplayTestCase(TransactionTestCase):
    """A transactional case that puts the seeded workspace back after flushing."""

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        restore_seed_workspace()


class SharedMigrationReplayTestCase(MigrationReplayTestCase):
    """Replay a migration once for a whole class instead of once per test.

    Subclasses build every fixture the class needs in :meth:`set_up_replay` and
    replay the migration there once. The per-test flush is then suspended, so
    the tests all read the single result; the database is cleared once when the
    class finishes, which is what keeps the next class isolated. Only subclass
    this where the tests read the replay rather than write over it — anything
    that mutates shared rows, or asserts on a state some other target produces,
    belongs in a class that replays per test.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.set_up_replay()
        except Exception:
            # setUpClass owns the class-level state from here, so a failure
            # part-way through has to hand it back before propagating.
            super().tearDownClass()
            raise

    @classmethod
    def set_up_replay(cls):
        """Build the class's fixtures and replay the migration over them."""
        raise NotImplementedError

    @classmethod
    def tearDownClass(cls):
        """Clear the shared rows the per-test flush was left to skip."""
        call_command(
            'flush',
            verbosity=0,
            interactive=False,
            database=connection.alias,
            reset_sequences=False,
            allow_cascade=False,
        )
        restore_seed_workspace()
        super().tearDownClass()

    def _fixture_teardown(self):
        """Keep the shared replay in place between the tests that read it."""
