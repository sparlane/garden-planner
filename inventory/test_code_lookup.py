"""REST contracts used by the shared pot resolver and Inventory navigation."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from labels.models import LabelCode
from labels.services import ensure_identity
from plantings.models import SpecificPlantLocation
from tests.factories import (
    make_inventory_item, make_numbered_container, make_seed_tray,
    make_specific_plant,
)
from workspaces.models import Workspace


class PotCodeLookupTests(TestCase):
    """Looking up history must not inherit destination availability filters."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('pot-lookup'))
        self.pot = make_numbered_container()
        self.code = ensure_identity(self.pot).codes.get(status=LabelCode.Status.ACTIVE)

    def units(self, **query):
        """Read the collection used by both number and asset-code resolution."""
        response = self.client.get('/inventory/serialized-units/', query)
        self.assertEqual(response.status_code, 200)
        return response.data

    def resolve(self, value):
        """Read a scan without applying the picker's placement refusals."""
        response = self.client.get('/labels/resolve/', {'value': value})
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_number_asset_code_and_label_identify_the_same_detail(self):
        """A typed number, case-insensitive asset code and QR share one unit."""
        numbered = self.units(number_from=self.pot.pk, number_to=self.pot.pk)
        coded = self.units(asset_code=self.pot.asset_code.lower())
        self.assertEqual([row['pk'] for row in numbered['results']], [self.pot.pk])
        self.assertEqual(coded['results'], numbered['results'])
        for value in (self.code.code, f'https://example.test/#/scan/{self.code.code}'):
            with self.subTest(value=value):
                resolved = self.resolve(value)
                self.assertEqual(resolved['status'], 'active')
                self.assertEqual(resolved['target']['target_type'], 'inventoryunit')
                self.assertEqual(resolved['target']['object_id'], self.pot.pk)
        detail = self.client.get(f'/inventory/serialized-units/{self.pot.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['pk'], self.pot.pk)

    def test_ambiguous_fragment_and_unissued_number_are_explicit(self):
        """The resolver can report the match count, and absence is not a 404."""
        second = make_numbered_container()
        matched = self.units(asset_code='ASSET-')
        self.assertEqual(matched['count'], 2)
        self.assertEqual({row['pk'] for row in matched['results']}, {self.pot.pk, second.pk})
        first = self.units(asset_code='ASSET-', page_size=1)
        next_page = self.units(asset_code='ASSET-', page_size=1, page=2)
        self.assertEqual(first['count'], 2)
        self.assertIsNotNone(first['next'])
        self.assertIsNone(next_page['next'])
        self.assertEqual(
            {row['pk'] for row in first['results'] + next_page['results']},
            {self.pot.pk, second.pk},
        )
        missing = max(self.pot.pk, second.pk) + 1
        self.assertEqual(self.units(number_from=missing, number_to=missing)['results'], [])
        self.assertEqual(self.resolve('ASSET-')['status'], 'unknown')

    def test_retired_absent_and_occupied_pots_remain_reachable(self):
        """History stays available when a pot is unsuitable for a new move."""
        retired = make_numbered_container(active=False)
        absent = make_numbered_container(current_location=None)
        SpecificPlantLocation.objects.create(
            specific_plant=make_specific_plant(),
            location_type=SpecificPlantLocation.CONTAINER_UNIT,
            container_unit=self.pot,
        )
        for pot in (retired, absent, self.pot):
            with self.subTest(pot=pot.pk):
                rows = self.units(number_from=pot.pk, number_to=pot.pk)['results']
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['pk'], pot.pk)
                code = ensure_identity(pot).codes.get(status=LabelCode.Status.ACTIVE)
                self.assertEqual(self.resolve(code.code)['target']['object_id'], pot.pk)
                detail = self.client.get(f'/inventory/serialized-units/{pot.pk}/')
                self.assertEqual(detail.status_code, 200)
        self.assertFalse(self.units(number_from=retired.pk, number_to=retired.pk)['results'][0]['active'])
        self.assertEqual(self.units(number_from=absent.pk, number_to=absent.pk)['results'][0]['physical_state'], 'retired')
        self.assertTrue(self.units(number_from=self.pot.pk, number_to=self.pot.pk)['results'][0]['in_use'])

    def test_tray_label_names_the_tray_screen(self):
        """A numbered tray is a navigable target, even though it is not a pot."""
        tray = make_seed_tray()
        code = ensure_identity(tray).codes.get(status=LabelCode.Status.ACTIVE)
        resolved = self.resolve(code.code)
        self.assertEqual(resolved['target']['target_type'], 'seedtray')
        self.assertEqual(resolved['target']['object_id'], tray.pk)
        self.assertEqual(resolved['deep_link'], f'/seedtrays/{tray.pk}')

    def test_other_workspace_does_not_leak_a_unit(self):
        """Foreign labels retain their explanation; collection lookups are scoped."""
        other = Workspace.objects.create(name='Other pot workspace')
        item = make_inventory_item(
            workspace=other, category=self.pot.item.category,
            tracking_mode=self.pot.item.tracking_mode,
            base_unit=self.pot.item.base_unit,
        )
        foreign = make_numbered_container(item=item)
        code = ensure_identity(foreign).codes.get(status=LabelCode.Status.ACTIVE)
        self.assertEqual(self.resolve(code.code), {
            'status': 'wrong_workspace',
            'message': 'This code belongs to another workspace.',
        })
        self.assertEqual(self.units(asset_code=foreign.asset_code)['results'], [])
        self.assertEqual(self.units(number_from=foreign.pk, number_to=foreign.pk)['results'], [])
