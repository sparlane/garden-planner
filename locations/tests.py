"""Contract tests for the shared physical location catalog."""

from seeds.services import ensure_packet_inventory_identity
from tests.api import RESTContractTestCase
from tests.factories import make_location, make_seed_packet, make_stock_lot
from workspaces.models import get_current_workspace

from .models import Location


class LocationRestTests(RESTContractTestCase):
    """The catalog is workspace-scoped, filterable, and safe to retire."""

    url = '/locations/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()

    def test_authentication_is_required(self):
        """The catalog names a workspace's real premises, so it is not public."""
        self.assert_authentication_required([self.url])

    def test_a_location_round_trips_through_the_api(self):
        """An operator can name a place and read back what they named."""
        self.assert_create_retrieve(
            self.url,
            {
                'name': 'Temporary receiving',
                'code': 'TEMP-RECV',
                'location_type': Location.LocationType.RECEIVING,
            },
        )

    def test_locations_filter_by_status_and_type(self):
        """Pickers ask for the places a workflow can actually use."""
        growing = make_location(
            workspace=self.workspace,
            location_type=Location.LocationType.GROWING,
        )
        make_location(
            workspace=self.workspace,
            location_type=Location.LocationType.STORAGE,
        )
        retired = make_location(
            workspace=self.workspace,
            location_type=Location.LocationType.GROWING,
            active=False,
        )

        response = self.client.get(
            self.url,
            {'active': 'true', 'location_type': 'growing'},
        )
        self.assertEqual(response.status_code, 200)
        returned = [location['pk'] for location in response.data]
        self.assertEqual(returned, [growing.pk])
        self.assertNotIn(retired.pk, returned)

    def test_an_unused_location_is_deletable(self):
        """A place that never held anything leaves no history worth keeping."""
        location = make_location(workspace=self.workspace)
        response = self.client.delete(f'{self.url}{location.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Location.objects.filter(pk=location.pk).exists())

    def test_a_used_location_must_be_deactivated_rather_than_deleted(self):
        """Deleting a place stock passed through would orphan the ledger."""
        location = make_location(workspace=self.workspace)
        make_stock_lot(workspace=self.workspace, location=location)

        response = self.client.delete(f'{self.url}{location.pk}/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.data)

        deactivated = self.client.patch(
            f'{self.url}{location.pk}/',
            {'active': False},
            format='json',
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.data)
        self.assertFalse(deactivated.data['active'])

    def test_seed_packet_containers_stay_under_the_seed_workflow(self):
        """A packet's container is created and retired with the packet itself."""
        packet = make_seed_packet(workspace=self.workspace)
        ensure_packet_inventory_identity(packet)
        packet.refresh_from_db()
        container = packet.storage_location
        self.assertIsNotNone(container)

        rejected = self.client.patch(
            f'{self.url}{container.pk}/',
            {'name': 'Renamed by hand'},
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('location_type', rejected.data)

        deleted = self.client.delete(f'{self.url}{container.pk}/')
        self.assertEqual(deleted.status_code, 400)
        self.assertIn('location', deleted.data)

    def test_a_new_location_cannot_claim_the_packet_type(self):
        """Only the seed workflow may mint packet containers."""
        response = self.client.post(
            self.url,
            {
                'name': 'Hand-made packet',
                'code': 'HAND-PACKET',
                'location_type': Location.LocationType.SEED_PACKET,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('location_type', response.data)
