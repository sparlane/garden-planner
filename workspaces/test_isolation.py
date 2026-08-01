"""Cross-workspace isolation tests for catalogs, gardens, and trays."""

# pylint: disable=duplicate-code,too-many-instance-attributes

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
from supplies.models import Supplier

from .models import Workspace, get_current_workspace


class ResourceIsolationTests(APITestCase):
    """Current-workspace endpoints neither reveal nor accept foreign records."""

    def setUp(self):
        super().setUp()
        self.current = get_current_workspace()
        self.other = Workspace.objects.create(name='Other workspace')
        user = get_user_model().objects.create_user(username='isolation-user')
        self.client.force_authenticate(user)
        self.client.force_login(user)

        self.supplier = Supplier.objects.create(
            workspace=self.other,
            name='Other supplier',
        )
        self.family = PlantFamily.objects.create(
            workspace=self.other,
            name='Other family',
        )
        self.plant = Plant.objects.create(
            workspace=self.other,
            family=self.family,
            name='Other plant',
        )
        self.variety = PlantVariety.objects.create(
            workspace=self.other,
            plant=self.plant,
            name='Other variety',
        )
        self.seeds = Seeds.objects.create(
            workspace=self.other,
            supplier=self.supplier,
            plant_variety=self.variety,
        )
        self.packet = SeedPacket.objects.create(
            workspace=self.other,
            seeds=self.seeds,
        )
        self.area = GardenArea.objects.create(
            workspace=self.other,
            name='Other area',
            size_x=10,
            size_y=10,
        )
        self.bed = GardenBed.objects.create(
            workspace=self.other,
            area=self.area,
            name='Other bed',
            placement_x=0,
            placement_y=0,
            size_x=5,
            size_y=5,
        )
        self.row = GardenRow.objects.create(
            workspace=self.other,
            bed=self.bed,
            name='Other row',
            placement_x=0,
            placement_y=0,
            size_x=5,
            size_y=1,
        )
        self.square = GardenSquare.objects.create(
            workspace=self.other,
            bed=self.bed,
            name='Other square',
            placement_x=0,
            placement_y=0,
            size_x=1,
            size_y=1,
        )
        self.tray_model = SeedTrayModel.objects.create(
            workspace=self.other,
            identifier='Other model',
            height=10,
            x_size=20,
            y_size=20,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )
        self.tray = SeedTray.objects.create(
            workspace=self.other,
            model=self.tray_model,
        )
        self.cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=0,
            y_position=0,
        )

    def test_lists_and_details_hide_other_workspace_records(self):
        """All direct catalog and geometry endpoints present a local namespace."""
        resources = (
            ('/supplies/supplier/', self.supplier.pk),
            ('/plants/family/', self.family.pk),
            ('/plants/plant/', self.plant.pk),
            ('/plants/variety/', self.variety.pk),
            ('/seeds/seeds/', self.seeds.pk),
            ('/seeds/packets/', self.packet.pk),
            ('/seeds/packets/all/', self.packet.pk),
            ('/garden/areas/', self.area.pk),
            ('/garden/beds/', self.bed.pk),
            ('/garden/rows/', self.row.pk),
            ('/garden/squares/', self.square.pk),
            ('/seedtrays/seedtraymodels/', self.tray_model.pk),
            ('/seedtrays/seedtrays/', self.tray.pk),
            ('/seedtrays/seedtraycells/', self.cell.pk),
        )
        for url, record_pk in resources:
            with self.subTest(url=url):
                list_response = self.client.get(url)
                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(list_response.data, [])
                detail_response = self.client.get(f'{url}{record_pk}/')
                self.assertEqual(detail_response.status_code, 404)

    def test_creates_are_bound_to_current_workspace(self):
        """Clients cannot choose ownership for newly created root records."""
        supplier_response = self.client.post(
            '/supplies/supplier/',
            {'name': 'Current supplier', 'workspace': self.other.pk},
            format='json',
        )
        area_response = self.client.post(
            '/garden/areas/',
            {
                'name': 'Current area',
                'size_x': 10,
                'size_y': 10,
                'workspace': self.other.pk,
            },
            format='json',
        )

        self.assertEqual(supplier_response.status_code, 201)
        self.assertEqual(area_response.status_code, 201)
        self.assertEqual(
            Supplier.objects.get(pk=supplier_response.data['pk']).workspace,
            self.current,
        )
        self.assertEqual(
            GardenArea.objects.get(pk=area_response.data['pk']).workspace,
            self.current,
        )

    def test_cross_workspace_foreign_keys_are_rejected(self):
        """Every catalog, geometry, and tray parent field is workspace-scoped."""
        requests = (
            ('/plants/plant/', {'family': self.family.pk, 'name': 'Plant'}),
            ('/plants/variety/', {'plant': self.plant.pk, 'name': 'Variety'}),
            (
                '/seeds/seeds/',
                {'supplier': self.supplier.pk, 'plant_variety': self.variety.pk},
            ),
            ('/seeds/packets/', {'seeds': self.seeds.pk}),
            (
                '/garden/beds/',
                {
                    'area': self.area.pk,
                    'name': 'Bed',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 1,
                    'size_y': 1,
                },
            ),
            (
                '/garden/rows/',
                {
                    'bed': self.bed.pk,
                    'name': 'Row',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 1,
                    'size_y': 1,
                },
            ),
            (
                '/garden/squares/',
                {
                    'bed': self.bed.pk,
                    'name': 'Square',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 1,
                    'size_y': 1,
                },
            ),
            ('/seedtrays/seedtrays/', {'model': self.tray_model.pk}),
            (
                '/seedtrays/seedtraycells/',
                {'tray': self.tray.pk, 'x_position': 1, 'y_position': 1},
            ),
        )
        for url, payload in requests:
            with self.subTest(url=url):
                response = self.client.post(url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def test_nested_and_html_tray_routes_hide_foreign_parent(self):
        """Parent resolution is scoped before nested or HTML rendering."""
        nested = self.client.get(
            f'/seedtrays/seedtrays/{self.tray.pk}/cells/',
        )
        detail = self.client.get(f'/seedtrays/seedtray/{self.tray.pk}/')

        self.assertEqual(nested.status_code, 404)
        self.assertEqual(detail.status_code, 404)
