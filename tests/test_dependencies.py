"""Guard the lint signal and the service/REST dependency boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent


class DependencyTests(TestCase):
    """Exercise imports in fresh processes, independent of test discovery order."""

    def test_new_cycle_is_reported_beside_a_suppressed_edge(self):
        """An approved edge must not mask a different cycle in the same module."""
        with TemporaryDirectory(prefix='garden-import-check-') as directory:
            package = Path(directory) / 'cycle_probe'
            package.mkdir()
            (package / '__init__.py').write_text('"""Lint probe."""\n')
            (package / 'first.py').write_text(
                '"""Two independent dependency edges."""\n'
                '# pylint: disable=unused-import\n'
                '# Deliberate callback edge; the other import remains checked.\n'
                'from . import approved  # pylint: disable=cyclic-import\n'
                'from . import accidental\n',
            )
            for name in ('approved', 'accidental'):
                (package / f'{name}.py').write_text(
                    '"""Return dependency for the lint probe."""\n'
                    'from . import first  # pylint: disable=unused-import\n',
                )
            result = subprocess.run(
                [sys.executable, '-m', 'pylint', f'--rcfile={ROOT / ".pylintrc"}',
                 '--output-format=json', '--reports=no', '--score=no',
                 '--persistent=no', str(package)],
                cwd=ROOT, capture_output=True, text=True, check=False,
                env={**os.environ, 'PYTHONPATH': str(ROOT)},
            )
        self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
        cycles = [message for message in json.loads(result.stdout)
                  if message['symbol'] == 'cyclic-import']
        self.assertEqual(len(cycles), 1, result.stdout)
        self.assertIn('cycle_probe.accidental', cycles[0]['message'])
        self.assertNotIn('cycle_probe.approved', cycles[0]['message'])

    def test_domain_services_do_not_load_rest_modules(self):
        """Stocktakes, health, sales, and bulk work can load without viewsets."""
        result = subprocess.run(
            [sys.executable, '-c',
             'import os, sys, importlib; '
             'os.environ["DJANGO_SETTINGS_MODULE"] = "gp.settings"; '
             'import django; django.setup(); '
             '[importlib.import_module(name) for name in '
             '("inventory.stocktakes", "health.operations", "sales.commerce", '
             '"plantings.bulk_operations")]; '
             'assert "plantings.rest" not in sys.modules; '
             'assert "applications.rest" not in sys.modules'],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
