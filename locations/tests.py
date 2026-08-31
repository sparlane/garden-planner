"""Contract tests for the shared physical location catalog."""

from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import TestCase

from seeds.services import ensure_packet_inventory_identity
from tests.api import RESTContractTestCase
from tests.factories import make_location, make_seed_packet, make_stock_lot
from workspaces.models import Workspace, get_current_workspace

from .models import Location, location_full_name


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
        returned = [location['pk'] for location in response.data['results']]
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


class LocationHierarchyTests(TestCase):
    """Nesting places lets one question reach a whole greenhouse at once."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()

    def nest(self, name, parent=None):
        """Create one named location under an optional parent."""
        return make_location(
            workspace=self.workspace,
            name=name,
            location_type=Location.LocationType.BENCH,
            parent=parent,
        )

    def test_a_new_location_learns_its_own_path(self):
        """A path exists from the first save, because lookups depend on it."""
        root = self.nest('Greenhouse 1')
        self.assertEqual(root.path, f'/{root.pk}/')
        root.refresh_from_db()
        self.assertEqual(root.path, f'/{root.pk}/')

    def test_a_child_carries_its_ancestors_in_its_path(self):
        """The path is what makes an ancestor chain one query rather than many."""
        root = self.nest('Greenhouse 1')
        bench = self.nest('Bench A', parent=root)
        bay = self.nest('Bay 2', parent=bench)

        self.assertEqual(bay.path, f'/{root.pk}/{bench.pk}/{bay.pk}/')
        self.assertEqual(bay.ancestor_ids, [root.pk, bench.pk])
        self.assertEqual(
            sorted(root.descendants().values_list('pk', flat=True)),
            sorted([bench.pk, bay.pk]),
        )

    def test_a_prefix_match_does_not_catch_a_sibling_sharing_leading_digits(self):
        """A path of /1/ must not match /12/; the trailing separator prevents it."""
        short = self.nest('Short')
        longer = self.nest('Longer')
        Location.objects.filter(pk=short.pk).update(path='/1/')
        Location.objects.filter(pk=longer.pk).update(path='/12/')
        short.refresh_from_db()

        self.assertEqual(list(short.descendants()), [])

    def test_reparenting_moves_the_whole_subtree(self):
        """Filing a bench under the wrong greenhouse must be correctable."""
        first = self.nest('Greenhouse 1')
        second = self.nest('Greenhouse 2')
        bench = self.nest('Bench A', parent=first)
        bay = self.nest('Bay 2', parent=bench)

        bench.parent = second
        bench.save()

        bay.refresh_from_db()
        self.assertEqual(bay.path, f'/{second.pk}/{bench.pk}/{bay.pk}/')
        self.assertEqual(bay.ancestor_ids, [second.pk, bench.pk])
        self.assertEqual(list(first.descendants()), [])

    def test_a_location_cannot_be_moved_inside_itself(self):
        """A cycle would make the ancestor chain loop forever."""
        root = self.nest('Greenhouse 1')
        bench = self.nest('Bench A', parent=root)

        root.parent = bench
        with self.assertRaises(ValidationError) as caught:
            root.save()
        self.assertIn('parent', caught.exception.message_dict)

    def test_a_location_cannot_be_its_own_parent(self):
        """The shortest cycle is still a cycle."""
        root = self.nest('Greenhouse 1')
        root.parent = root
        with self.assertRaises(ValidationError) as caught:
            root.save()
        self.assertIn('parent', caught.exception.message_dict)

    def test_a_parent_must_share_the_workspace(self):
        """A greenhouse cannot hold another workspace's bench."""
        other = Workspace.objects.create(name='Another nursery')
        outsider = make_location(workspace=other)
        bench = Location(
            workspace=self.workspace,
            name='Bench A',
            code='BENCH-A',
            location_type=Location.LocationType.BENCH,
            parent=outsider,
        )
        with self.assertRaises(ValidationError) as caught:
            bench.save()
        self.assertIn('parent', caught.exception.message_dict)

    def test_the_full_name_reads_the_way_an_operator_says_it(self):
        """"Bay 2" is ambiguous across three greenhouses; the path is not."""
        root = self.nest('Greenhouse 1')
        bench = self.nest('Bench A', parent=root)
        bay = self.nest('Bay 2', parent=bench)

        self.assertEqual(location_full_name(bay), 'Greenhouse 1 / Bench A / Bay 2')


class LocationCapacityFieldTests(TestCase):
    """A capacity is meaningless without the dimension it is measured in."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()

    def test_a_basis_requires_a_value(self):
        """Saying a bench is counted in trays without saying how many says nothing."""
        with self.assertRaises(ValidationError) as caught:
            make_location(
                workspace=self.workspace,
                capacity_basis=Location.CapacityBasis.TRAYS,
            )
        self.assertIn('capacity_value', caught.exception.message_dict)

    def test_an_untracked_location_takes_no_value(self):
        """A number with no dimension would be compared against nothing."""
        with self.assertRaises(ValidationError) as caught:
            make_location(
                workspace=self.workspace,
                capacity_value=Decimal('40'),
            )
        self.assertIn('capacity_value', caught.exception.message_dict)

    def test_every_basis_can_be_recorded(self):
        """Each dimension in the vocabulary must be storable, area included."""
        for basis in Location.CapacityBasis:
            if basis == Location.CapacityBasis.NONE:
                continue
            with self.subTest(basis=basis):
                location = make_location(
                    workspace=self.workspace,
                    capacity_basis=basis,
                    capacity_value=Decimal('12'),
                )
                self.assertEqual(location.capacity_basis, basis)

    def test_area_is_an_enforced_capacity_basis(self):
        """Container footprints make a bench's occupied area measurable."""
        self.assertIn(
            Location.CapacityBasis.AREA,
            Location.ENFORCED_BASES,
        )

    def test_system_managed_locations_take_no_parent_or_capacity(self):
        """A seed packet is a container, not somewhere plants are grown."""
        root = make_location(workspace=self.workspace)
        packet_location = Location(
            workspace=self.workspace,
            name='Seed packet 1',
            code='SEED-PACKET-TEST',
            location_type=Location.LocationType.SEED_PACKET,
            parent=root,
        )
        with self.assertRaises(ValidationError) as caught:
            packet_location.save()
        self.assertIn('location_type', caught.exception.message_dict)


class LocationPathBackfillTests(TestCase):
    """The migration that gave the flat catalog its paths."""

    def test_the_backfill_gives_every_location_its_own_root_path(self):
        """A blank path would prefix-match the entire catalog."""
        migration = import_module('locations.migrations.0004_backfill_location_paths')
        workspace = get_current_workspace()
        first = make_location(workspace=workspace)
        second = make_location(workspace=workspace)
        Location.objects.filter(pk__in=[first.pk, second.pk]).update(path='')

        migration.backfill_location_paths(django_apps, None)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.path, f'/{first.pk}/')
        self.assertEqual(second.path, f'/{second.pk}/')
