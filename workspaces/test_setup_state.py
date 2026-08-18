"""How a workspace records whether guided garden setup has been dealt with."""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase

from .current import get_current_workspace
from .models import Workspace


class GardenSetupStateTests(RESTContractTestCase):
    """The gardener's answer about setup, not the state of their data."""

    url = '/settings/workspace/'

    def test_a_workspace_starts_pending(self):
        """Nobody has been asked yet, so the offer is still open."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['garden_setup_state'], 'pending')

    def test_setup_can_be_skipped(self):
        """Declining once means not being asked again."""
        response = self.client.patch(self.url, {'garden_setup_state': 'skipped'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_current_workspace().garden_setup_state, Workspace.SetupState.SKIPPED)

    def test_setup_can_be_completed(self):
        """Finishing the wizard is recorded so the prompt stops."""
        self.client.patch(self.url, {'garden_setup_state': 'complete'}, format='json')
        self.assertEqual(get_current_workspace().garden_setup_state, Workspace.SetupState.COMPLETE)

    def test_a_completed_setup_can_be_reopened(self):
        """Adding another area later is a normal thing to want."""
        self.client.patch(self.url, {'garden_setup_state': 'complete'}, format='json')
        response = self.client.patch(self.url, {'garden_setup_state': 'pending'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_current_workspace().garden_setup_state, Workspace.SetupState.PENDING)

    def test_an_unknown_state_is_refused(self):
        """The vocabulary is controlled, like every other choice field."""
        response = self.client.patch(self.url, {'garden_setup_state': 'halfway'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('garden_setup_state', response.data)
