"""Nursery-only routes stay refused server-side after the Garden nav regroup.

Basic and Advanced Garden experience are pure presentation (see
`GardenExperienceTests`): nothing about them should touch which routes
`RequireWorkspaceModeMixin` refuses. This is a regression check on the
existing Garden/Nursery gate, not new authorization logic for the new
preference.
"""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase

from .current import get_current_workspace
from .models import Workspace

#: One representative registered route per nursery-gated app.
NURSERY_ONLY_URLS = (
    '/sales/customers/',
    '/reports/dashboard/',
    '/plantings/growth-stages/',
    '/plantings/planning-assumptions/',
    '/plantings/cohorts/',
    '/plantings/register/',
    '/tax/gst/registrations/',
    '/tax/gst/status/',
    '/tax/gst/period-closures/',
    '/tax/gst/basis-transitions/',
)

SHARED_OPERATION_URLS = (
    '/health/observation-types/',
    '/work/rules/',
)


class NurseryOnlyRouteAuthorizationTests(RESTContractTestCase):
    """A Garden workspace cannot reach nursery-only routes by any URL."""

    def _assert_all_refused(self):
        for url in NURSERY_ONLY_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403, response.data)

    def test_basic_garden_refuses_every_nursery_only_route(self):
        """The default Garden experience is still gated, not just hidden."""
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.GARDEN
        workspace.garden_experience = Workspace.GardenExperience.BASIC
        workspace.save()
        self._assert_all_refused()

    def test_advanced_garden_still_refuses_every_nursery_only_route(self):
        """Advanced widens detail, not which product the workspace runs."""
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.GARDEN
        workspace.garden_experience = Workspace.GardenExperience.ADVANCED
        workspace.save()
        self._assert_all_refused()

    def test_nursery_mode_allows_every_route(self):
        """The gate flips on `mode`, confirming the refusal above is real."""
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.NURSERY
        workspace.save()
        for url in NURSERY_ONLY_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.data)

    def test_garden_mode_allows_health_and_work_routes(self):
        """Garden workspace actions can report problems and manage due care."""
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.GARDEN
        workspace.save()
        for url in SHARED_OPERATION_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(response.data)
