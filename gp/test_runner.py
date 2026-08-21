"""Test runner that refuses to report a silent skip as a passing run.

Roughly two dozen tests across the ledger, sales, stocktake, health, and
seed-tray apps are decorated ``@skipUnlessDBFeature('has_select_for_update')``.
On SQLite that feature is false, so all of them skip and unittest reports
``OK (skipped=21)`` — a green run in which none of the row-locking guarantees
were exercised. This runner names what was skipped and why, and can be told to
fail instead so the count cannot grow again unnoticed.
"""
import os
import sys

from django.db import connection
from django.test.runner import DiscoverRunner


# The reason text Django's skipUnlessDBFeature/skipIfDBFeature record.
FEATURE_SKIP_PREFIX = "Database doesn't support feature(s)"

TRUE_VALUES = {"1", "true", "yes", "on"}


def fail_on_skip():
    """Report whether a skipped test should fail the run."""
    return os.environ.get("GP_FAIL_ON_SKIP", "").lower() in TRUE_VALUES


class GardenTestRunner(DiscoverRunner):
    """Annotate the unittest summary with the cost of the current backend."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor = connection.vendor
        self.row_locking = True

    def setup_databases(self, **kwargs):
        """Record what the backend can do while a connection is open."""
        old_config = super().setup_databases(**kwargs)
        self.vendor = connection.vendor
        self.row_locking = connection.features.has_select_for_update
        return old_config

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        skipped = [reason for _, reason in getattr(result, "skipped", [])]
        if not skipped:
            return failures

        feature_skips = [
            reason for reason in skipped if reason.startswith(FEATURE_SKIP_PREFIX)
        ]
        self._report(skipped, feature_skips)
        if fail_on_skip():
            return failures + len(skipped)
        return failures

    def _report(self, skipped, feature_skips):
        """Explain the skips on stderr, next to the summary they qualify."""
        lines = []
        if feature_skips and not self.row_locking:
            lines.append(
                f"WARNING: {len(feature_skips)} concurrency tests did not run. "
                f"The {self.vendor} backend has no has_select_for_update, so "
                "the row-locking behaviour protecting the inventory ledger, "
                "sales allocations, stocktake corrections, and quarantine "
                "transitions was not exercised."
            )
            lines.append(
                "         Run ./test-venv.sh --postgresql for the suite CI runs."
            )
        elif feature_skips:
            lines.append(
                f"WARNING: {len(feature_skips)} tests were skipped for missing "
                f"{self.vendor} database features."
            )

        other = len(skipped) - len(feature_skips)
        if other:
            lines.append(f"WARNING: {other} further tests were skipped.")

        if fail_on_skip():
            lines.append(
                "ERROR: GP_FAIL_ON_SKIP is set, so a skipped test fails the run."
            )

        for line in lines:
            print(line, file=sys.stderr)
