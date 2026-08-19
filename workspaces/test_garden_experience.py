"""How a workspace records how much detail Garden-profile screens show."""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase

from .current import get_current_workspace
from .models import Workspace


class GardenExperienceTests(RESTContractTestCase):
    """Basic hides business detail; Advanced shows it. Nursery always sees it."""

    url = '/settings/workspace/'

    def test_a_workspace_starts_basic(self):
        """A new Garden workspace defaults to the simplest experience."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['garden_experience'], 'basic')

    def test_experience_can_be_set_to_advanced(self):
        """A gardener who wants full detail can ask for it."""
        response = self.client.patch(self.url, {'garden_experience': 'advanced'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_current_workspace().garden_experience, Workspace.GardenExperience.ADVANCED)

    def test_an_unknown_experience_is_refused(self):
        """The vocabulary is controlled, like every other choice field."""
        response = self.client.patch(self.url, {'garden_experience': 'expert'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('garden_experience', response.data)

    def test_nursery_is_advanced_regardless_of_the_stored_value(self):
        """Nursery workflows need the underlying records, so it is never Basic."""
        workspace = get_current_workspace()
        workspace.mode = Workspace.Mode.NURSERY
        workspace.garden_experience = Workspace.GardenExperience.BASIC
        workspace.save()
        self.assertTrue(get_current_workspace().is_advanced)

    def test_garden_basic_is_not_advanced(self):
        """The default Garden experience is the simplified one."""
        workspace = get_current_workspace()
        self.assertEqual(workspace.mode, Workspace.Mode.GARDEN)
        self.assertFalse(workspace.is_advanced)

    def test_garden_advanced_is_advanced(self):
        """A gardener who opted in sees the same detail a nursery does."""
        workspace = get_current_workspace()
        workspace.garden_experience = Workspace.GardenExperience.ADVANCED
        workspace.save()
        self.assertTrue(get_current_workspace().is_advanced)
