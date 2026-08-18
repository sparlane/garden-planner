"""Household-facing additions to the shared location catalog."""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase
from workspaces.current import get_current_workspace
from workspaces.models import Workspace

from .defaults import ensure_household_locations
from .models import Location, location_full_name


class ContainerLocationTests(RESTContractTestCase):
    """A pot is a place a household stands a plant in."""

    def test_a_container_location_can_be_created(self):
        """A gardener's pots belong in the same catalog as a nursery's benches."""
        created = self.assert_create_retrieve(
            '/locations/',
            {
                'name': 'Patio pots',
                'code': 'PATIO-POTS',
                'location_type': 'container',
            },
        )
        self.assertEqual(created['location_type'], 'container')

    def test_containers_can_be_filtered_for(self):
        """The type filter accepts the new value like any other."""
        Location.objects.create(name='Patio pots', code='PATIO-POTS', location_type='container')
        Location.objects.create(name='Shed', code='SHED', location_type='storage')
        response = self.client.get('/locations/?location_type=container')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['code'] for row in response.data], ['PATIO-POTS'])


class HouseholdDefaultsTests(RESTContractTestCase):
    """Installing the places a household garden needs before it can work."""

    def test_the_ordinary_places_are_installed(self):
        """A gardener who never opens the catalog still has somewhere to put things."""
        locations = ensure_household_locations(get_current_workspace())
        self.assertEqual(
            [location.code for location in locations],
            ['GARDEN', 'SHED', 'SEED-STORE', 'POTTING-BENCH', 'HOLDING'],
        )

    def test_the_places_are_nested(self):
        """A shed is in the garden and the seed store is in the shed."""
        ensure_household_locations(get_current_workspace())
        seed_store = Location.objects.get(code='SEED-STORE')
        self.assertEqual(location_full_name(seed_store), 'Garden / Shed / Seed store')

    def test_installing_twice_creates_nothing_new(self):
        """Leaving and resuming the wizard does not double up the shed."""
        workspace = get_current_workspace()
        ensure_household_locations(workspace)
        before = Location.objects.count()
        ensure_household_locations(workspace)
        self.assertEqual(Location.objects.count(), before)

    def test_a_renamed_place_is_left_alone(self):
        """The gardener's own wording survives a second visit to this step."""
        workspace = get_current_workspace()
        ensure_household_locations(workspace)
        shed = Location.objects.get(code='SHED')
        shed.name = 'Back shed'
        shed.save()
        ensure_household_locations(workspace)
        shed.refresh_from_db()
        self.assertEqual(shed.name, 'Back shed')

    def test_each_workspace_gets_its_own(self):
        """The codes are unique per workspace, not across the deployment."""
        other = Workspace.objects.create(name='Other workspace')
        ensure_household_locations(get_current_workspace())
        ensure_household_locations(other)
        self.assertEqual(Location.objects.filter(code='GARDEN').count(), 2)


class HouseholdLocationsRESTTests(RESTContractTestCase):
    """The setup wizard asks for the household places over HTTP."""

    url = '/garden/setup/household-locations/'

    def test_posting_installs_and_reports_them(self):
        """The response names what the gardener now has, not only what changed."""
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            [row['code'] for row in response.data],
            ['GARDEN', 'SHED', 'SEED-STORE', 'POTTING-BENCH', 'HOLDING'],
        )

    def test_posting_again_reports_the_same_places(self):
        """Resuming the wizard at this step is safe and says so."""
        first = self.client.post(self.url, {}, format='json')
        second = self.client.post(self.url, {}, format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(
            [row['pk'] for row in first.data],
            [row['pk'] for row in second.data],
        )

    def test_installing_requires_authentication(self):
        """Anonymous callers cannot write to the catalog."""
        self.client.force_authenticate(user=None)
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
