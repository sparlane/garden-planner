"""REST contract tests for inventory catalog resources."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspaces.models import Workspace, get_current_workspace

from .models import InventoryItem, ItemUnitConversion
from .units import UnitCode


class InventoryRestTests(APITestCase):
    """Inventory APIs are scoped, typed, filterable, and non-destructive."""

    item_url = '/inventory/items/'
    conversion_url = '/inventory/conversions/'
    units_url = '/inventory/units/'

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username='inventory-api-user',
        )
        self.client.force_authenticate(self.user)

    def create_item(self, **overrides):
        """Create a valid API item and return its response."""
        payload = {
            'name': 'Propagation media',
            'sku': 'MEDIA-API',
            'category': InventoryItem.Category.GROWING_MEDIA,
            'base_unit': UnitCode.MILLILITRE,
            'default_usage_basis': InventoryItem.UsageBasis.CELL_VOLUME,
        }
        payload.update(overrides)
        return self.client.post(self.item_url, payload, format='json')

    def test_authentication_is_required_for_every_collection(self):
        """The shared deployment catalog is not anonymously readable."""
        self.client.force_authenticate(user=None)
        for url in (self.units_url, self.item_url, self.conversion_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_unit_registry_returns_stable_dimension_metadata(self):
        """Clients receive controlled choices without editable unit rows."""
        response = self.client.get(self.units_url)
        self.assertEqual(response.status_code, 200)
        units = {unit['code']: unit for unit in response.data}
        self.assertEqual(
            set(units),
            {'each', 'seed', 'seed_cluster', 'ml', 'l', 'g', 'kg', 'm2'},
        )
        self.assertEqual(units['l']['dimension'], 'volume')
        self.assertEqual(units['l']['reference_unit'], 'ml')
        self.assertEqual(units['l']['to_reference_multiplier'], '1000')
        self.assertEqual(self.client.post(self.units_url, {}, format='json').status_code, 405)

    def test_create_returns_dimensions_decimals_and_category_tracking_default(self):
        """API values are sufficient to render compatible usage forms."""
        response = self.create_item()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['base_unit'], 'ml')
        self.assertEqual(response.data['base_unit_dimension'], 'volume')
        self.assertIsNone(response.data['default_usage_rate'])
        self.assertIsNone(response.data['usage_rate_unit_dimension'])
        self.assertEqual(response.data['tracking_mode'], 'lot')
        self.assertNotIn('workspace', response.data)

        tray = self.create_item(
            name='Reusable propagation tray',
            sku='TRAY-API',
            category=InventoryItem.Category.TRAY,
            base_unit=UnitCode.EACH,
            default_usage_basis=InventoryItem.UsageBasis.MANUAL,
            default_usage_rate=None,
            usage_rate_unit=None,
        )
        self.assertEqual(tray.status_code, 201, tray.data)
        self.assertEqual(tray.data['tracking_mode'], 'serialized')

    def test_usage_validation_rejects_wrong_dimension(self):
        """Surface-area rates cannot use count or volume denominators."""
        response = self.create_item(
            default_usage_basis=InventoryItem.UsageBasis.SURFACE_AREA,
            default_usage_rate='2',
            usage_rate_unit=UnitCode.EACH,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('usage_rate_unit', response.data)

    def test_cell_volume_uses_tray_measurements_without_an_item_rate(self):
        """Cell volume is converted from each selected tray into a volume item."""
        response = self.create_item(
            default_usage_rate='1',
            usage_rate_unit=UnitCode.MILLILITRE,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('default_usage_rate', response.data)
        self.assertIn('usage_rate_unit', response.data)

        response = self.create_item(
            sku='MASS-CELL-VOLUME',
            base_unit=UnitCode.GRAM,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('base_unit', response.data)

    def test_item_filters_and_inactive_selector_contract(self):
        """Catalog screens and future selectors can request precise subsets."""
        active = self.create_item().data
        inactive = self.create_item(
            name='Retired labels',
            sku='LABEL-OLD',
            category=InventoryItem.Category.LABEL,
            base_unit=UnitCode.EACH,
            active=False,
            default_usage_basis=InventoryItem.UsageBasis.MANUAL,
            default_usage_rate=None,
            usage_rate_unit=None,
        ).data

        response = self.client.get(self.item_url, {'active': 'true'})
        self.assertEqual([row['pk'] for row in response.data], [active['pk']])
        response = self.client.get(self.item_url, {'category': 'label'})
        self.assertEqual([row['pk'] for row in response.data], [inactive['pk']])
        response = self.client.get(self.item_url, {'search': 'propagation'})
        self.assertEqual([row['pk'] for row in response.data], [active['pk']])
        response = self.client.get(self.item_url, {'tracking_mode': 'serialized'})
        self.assertEqual(response.data, [])

    def test_item_deactivation_survives_and_delete_is_unsupported(self):
        """Historical item identity remains readable after deactivation."""
        item = self.create_item().data
        patch = self.client.patch(
            f"{self.item_url}{item['pk']}/",
            {'active': False},
            format='json',
        )
        self.assertEqual(patch.status_code, 200, patch.data)
        self.assertFalse(patch.data['active'])
        self.assertEqual(
            self.client.get(f"{self.item_url}{item['pk']}/").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"{self.item_url}{item['pk']}/").status_code,
            405,
        )

    def test_stock_history_rejects_identity_changes(self):
        """Posted item identity cannot be rewritten through PATCH."""
        item_data = self.create_item().data
        item = InventoryItem.objects.get(pk=item_data['pk'])
        item.mark_stock_history_started()

        for payload in (
            {'base_unit': UnitCode.LITRE},
            {'tracking_mode': InventoryItem.TrackingMode.SERIALIZED},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    f'{self.item_url}{item.pk}/',
                    payload,
                    format='json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(next(iter(payload)), response.data)

    def test_package_conversion_is_decimal_scoped_filterable_and_persistent(self):
        """Item package labels normalize into the selected item's base unit."""
        item = self.create_item().data
        response = self.client.post(
            self.conversion_url,
            {
                'item': item['pk'],
                'label': '40 L bag',
                'multiplier': '40000',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['multiplier'], '40000.000000000')
        self.assertEqual(response.data['base_unit'], 'ml')
        self.assertEqual(response.data['base_unit_dimension'], 'volume')

        filtered = self.client.get(
            self.conversion_url,
            {'item': item['pk'], 'active': 'true'},
        )
        self.assertEqual([row['pk'] for row in filtered.data], [response.data['pk']])

        patch = self.client.patch(
            f"{self.conversion_url}{response.data['pk']}/",
            {'active': False},
            format='json',
        )
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(
            self.client.delete(
                f"{self.conversion_url}{response.data['pk']}/",
            ).status_code,
            405,
        )

    def test_zero_and_negative_package_multipliers_are_rejected(self):
        """Package units always add a positive amount of base stock."""
        item = self.create_item().data
        for multiplier in ('0', '-1'):
            with self.subTest(multiplier=multiplier):
                response = self.client.post(
                    self.conversion_url,
                    {
                        'item': item['pk'],
                        'label': f'Invalid {multiplier}',
                        'multiplier': multiplier,
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn('multiplier', response.data)

    def test_workspace_scoping_hides_and_rejects_foreign_records(self):
        """Items and conversions remain in their workspace namespace."""
        other = Workspace.objects.create(name='Foreign inventory workspace')
        foreign_item = InventoryItem.objects.create(
            workspace=other,
            name='Foreign item',
            category=InventoryItem.Category.PACKAGING,
            base_unit=UnitCode.EACH,
        )
        foreign_conversion = ItemUnitConversion.objects.create(
            workspace=other,
            item=foreign_item,
            label='Foreign carton',
            multiplier=Decimal('12'),
        )

        self.assertEqual(self.client.get(self.item_url).data, [])
        self.assertEqual(self.client.get(self.conversion_url).data, [])
        self.assertEqual(
            self.client.get(f'{self.item_url}{foreign_item.pk}/').status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f'{self.conversion_url}{foreign_conversion.pk}/',
            ).status_code,
            404,
        )
        response = self.client.post(
            self.conversion_url,
            {
                'item': foreign_item.pk,
                'label': 'Cross-workspace package',
                'multiplier': '1',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('item', response.data)
        self.assertEqual(get_current_workspace().pk, 1)
