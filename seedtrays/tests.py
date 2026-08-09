"""
Tests for seed trays
"""
# pylint: disable=duplicate-code
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from inventory.models import InventoryUnit, StockMovement
from locations.models import Location
from plantings.models import SeedTrayPlanting
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_supplier,
)

from .models import SeedTray, SeedTrayCell, SeedTrayModel


class SeedTrayRESTContractTests(RESTContractTestCase):
    """Smoke tests for seed-tray REST resources."""

    def setUp(self):
        super().setUp()
        self.tray = make_seed_tray()
        self.other_tray = make_seed_tray()

    @property
    def list_urls(self):
        """Return global and nested seed-tray collection routes."""
        return (
            '/seedtrays/seedtraymodels/',
            '/seedtrays/seedtrays/',
            '/seedtrays/seedtraycells/',
            f'/seedtrays/seedtrays/{self.tray.pk}/cells/',
        )

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list seed-tray resources."""
        self.assert_authentication_required(self.list_urls)

    def test_list_routes_return_lists(self):
        """Authenticated seed-tray collections use the common list contract."""
        self.assert_list_contract(self.list_urls)

    def test_resources_round_trip(self):
        """Tray models, trays, and global cells survive create and retrieve."""
        tray_model = self.assert_create_retrieve(
            '/seedtrays/seedtraymodels/',
            {
                'identifier': 'propagation-6',
                'description': 'Six-cell propagation tray',
                'height': 6,
                'x_size': 30,
                'y_size': 20,
                'x_cells': 3,
                'y_cells': 2,
                'cell_size_ml': 45,
            },
        )
        supplier = make_supplier()
        location = Location.objects.create(
            name='Receipt store',
            code='RECEIPT-STORE',
            location_type=Location.LocationType.STORAGE,
        )
        response = self.client.post(
            f"/seedtrays/seedtraymodels/{tray_model['pk']}/receive/",
            {
                'supplier': supplier.pk,
                'received_date': '2026-08-02',
                'quantity': 1,
                'line_cost_ex_tax': '5.0000',
                'destination': location.pk,
                'notes': 'Spring sowing',
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        tray = response.data['trays'][0]
        retrieved = self.client.get(f"/seedtrays/seedtrays/{tray['pk']}/")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.data, tray)
        cell = SeedTrayCell.objects.get(
            tray_id=tray['pk'],
            x_position=2,
            y_position=1,
        )
        response = self.client.get(f'/seedtrays/seedtraycells/{cell.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'pk': cell.pk,
            'tray': tray['pk'],
            'x_position': 2,
            'y_position': 1,
        })

        self.assert_create_retrieve(
            '/seedtrays/seedtraycells/',
            {
                'tray': self.tray.pk,
                'x_position': 1,
                'y_position': 1,
            },
        )

    def test_nested_cell_routes_are_scoped_to_url_tray(self):
        """Nested list and detail routes cannot expose another tray's cells."""
        own_cell = make_seed_tray_cell(tray=self.tray)
        other_cell = make_seed_tray_cell(tray=self.other_tray)
        nested_url = f'/seedtrays/seedtrays/{self.tray.pk}/cells/'

        response = self.client.get(nested_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {cell['pk'] for cell in response.data},
            {own_cell.pk},
        )
        response = self.client.get(f'{nested_url}{own_cell.pk}/')
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f'{nested_url}{other_cell.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_bare_tray_creation_is_replaced_by_receiving(self):
        """A physical tray cannot bypass unit provenance and stock posting."""
        response = self.client.post(
            '/seedtrays/seedtrays/',
            {'model': self.tray.model_id},
        )
        self.assertEqual(response.status_code, 405)


class SeedTrayCellIntegrityTests(TestCase):
    """
    Tests for stable tray grids and bounded cell membership.
    """

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='tray-tester'))
        self.tray_model = SeedTrayModel.objects.create(
            identifier='two-by-two',
            height=10,
            x_size=20,
            y_size=20,
            x_cells=2,
            y_cells=2,
            cell_size_ml=40,
        )
        self.other_model = SeedTrayModel.objects.create(
            identifier='three-by-three',
            height=10,
            x_size=30,
            y_size=30,
            x_cells=3,
            y_cells=3,
            cell_size_ml=40,
        )
        self.tray = make_seed_tray(model=self.tray_model)
        self.other_tray = make_seed_tray(model=self.other_model)
        self.supplier = make_supplier()
        self.location = Location.objects.create(
            name='Tray receiving',
            code='TRAY-RECEIVING',
            location_type=Location.LocationType.RECEIVING,
        )

    def test_detail_view_requires_login(self):
        """Anonymous visitors cannot discover seed tray detail pages."""
        self.client.logout()

        response = self.client.get(f'/seedtrays/seedtray/{self.tray.pk}/')

        self.assertRedirects(
            response,
            f'/accounts/login/?next=/seedtrays/seedtray/{self.tray.pk}/',
        )

    def test_receiving_tray_generates_complete_cell_grid(self):
        """Receiving a tray generates every model cell exactly once."""
        response = self.client.post(
            f'/seedtrays/seedtraymodels/{self.other_model.pk}/receive/',
            data=json.dumps({
                'supplier': self.supplier.pk,
                'received_date': '2026-08-02',
                'quantity': 1,
                'line_cost_ex_tax': '6.0000',
                'destination': self.location.pk,
                'notes': 'Propagation tray',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        tray = SeedTray.objects.get(pk=response.json()['trays'][0]['pk'])
        self.assertEqual(
            set(tray.seedtraycell_set.values_list('x_position', 'y_position')),
            {
                (x_position, y_position)
                for x_position in range(self.other_model.x_cells)
                for y_position in range(self.other_model.y_cells)
            },
        )

    def test_receiving_multiple_trays_keeps_identity_and_cost_exact(self):
        """One receipt creates distinct units, trays, cells, and movement rows."""
        response = self.client.post(
            f'/seedtrays/seedtraymodels/{self.other_model.pk}/receive/',
            {
                'supplier': self.supplier.pk,
                'received_date': '2026-08-02',
                'quantity': 3,
                'line_cost_ex_tax': '10.0000',
                'destination': self.location.pk,
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        trays = response.data['trays']
        self.assertEqual(len(trays), 3)
        self.assertEqual(len({tray['inventory_unit'] for tray in trays}), 3)
        units = InventoryUnit.objects.filter(
            pk__in=[tray['inventory_unit'] for tray in trays],
        )
        self.assertEqual(sum(unit.acquisition_cost for unit in units), 10)
        self.assertEqual(
            StockMovement.objects.filter(unit__in=units, movement_type='receipt').count(),
            3,
        )
        self.assertTrue(all(
            SeedTrayCell.objects.filter(tray_id=tray['pk']).count() == 9
            for tray in trays
        ))

    def test_active_cultivation_blocks_loss_but_allows_transfer(self):
        """A tray stays on hand while occupied and cannot silently leave stock."""
        tray = self.tray
        packet = make_seed_packet()
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
            quantity=1,
            seed_tray=tray,
        )
        unit = tray.inventory_unit
        response = self.client.get(
            '/seedtrays/seedtrays/',
            {'in_use': 'true'},
        )
        self.assertIn(tray.pk, [row['pk'] for row in response.data])

        response = self.client.post(
            f'/inventory/serialized-units/{unit.pk}/loss/',
            {'reason': 'Incorrectly remove occupied tray'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('unit', response.data)

        destination = Location.objects.create(
            name='Growing bench',
            code='GROWING-BENCH',
            location_type=Location.LocationType.GROWING,
        )
        response = self.client.post(
            f'/inventory/serialized-units/{unit.pk}/transfer/',
            {'destination': destination.pk, 'reason': 'Move occupied tray'},
        )
        self.assertEqual(response.status_code, 201, response.data)
        planting.refresh_from_db()
        unit.refresh_from_db()
        self.assertFalse(planting.removed)
        self.assertEqual(unit.current_location_id, destination.pk)

    def test_nested_create_uses_url_tray_instead_of_payload_tray(self):
        """
        The nested resource parent is authoritative when the payload conflicts.
        """
        response = self.client.post(
            f'/seedtrays/seedtrays/{self.tray.pk}/cells/',
            data=json.dumps({
                'tray': self.other_tray.pk,
                'x_position': 0,
                'y_position': 1,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        cell = SeedTrayCell.objects.get(pk=response.json()['pk'])
        self.assertEqual(cell.tray, self.tray)

    def test_nested_create_rejects_position_outside_url_tray(self):
        """
        Bounds are checked against the URL tray, not a payload tray with a larger grid.
        """
        response = self.client.post(
            f'/seedtrays/seedtrays/{self.tray.pk}/cells/',
            data=json.dumps({
                'tray': self.other_tray.pk,
                'x_position': 2,
                'y_position': 0,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'x_position': ['Must be less than 2.']})
        self.assertFalse(SeedTrayCell.objects.exists())

    def test_nested_update_rejects_positions_outside_url_tray(self):
        """
        Partial updates validate both coordinates without changing the existing cell.
        """
        cell = SeedTrayCell.objects.create(
            tray=self.tray,
            x_position=0,
            y_position=0,
        )

        for field in ('x_position', 'y_position'):
            with self.subTest(field=field):
                response = self.client.patch(
                    f'/seedtrays/seedtrays/{self.tray.pk}/cells/{cell.pk}/',
                    data=json.dumps({field: 2}),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {field: ['Must be less than 2.']})
                cell.refresh_from_db()
                self.assertEqual((cell.x_position, cell.y_position), (0, 0))

    def test_global_create_rejects_position_outside_payload_tray(self):
        """
        Non-nested cell writes enforce the same coordinate bounds.
        """
        response = self.client.post(
            '/seedtrays/seedtraycells/',
            data=json.dumps({
                'tray': self.tray.pk,
                'x_position': 0,
                'y_position': 2,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'y_position': ['Must be less than 2.']})

    def test_global_create_rejects_negative_positions(self):
        """
        PositiveIntegerField validation enforces the lower coordinate bounds.
        """
        for field in ('x_position', 'y_position'):
            with self.subTest(field=field):
                payload = {
                    'tray': self.tray.pk,
                    'x_position': 0,
                    'y_position': 0,
                    field: -1,
                }
                response = self.client.post(
                    '/seedtrays/seedtraycells/',
                    data=json.dumps(payload),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {field: ['Ensure this value is greater than or equal to 0.']},
                )
                self.assertFalse(SeedTrayCell.objects.exists())

    def test_existing_cell_cannot_be_moved_to_another_tray(self):
        """
        Changing a cell's tray cannot invalidate plantings that refer to its identity.
        """
        cell = SeedTrayCell.objects.create(tray=self.tray, x_position=0, y_position=0)

        response = self.client.patch(
            f'/seedtrays/seedtraycells/{cell.pk}/',
            data=json.dumps({'tray': self.other_tray.pk}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        cell.refresh_from_db()
        self.assertEqual(cell.tray, self.tray)

    def test_cell_dimensions_cannot_change_after_a_tray_exists(self):
        """
        Shrinking or reshaping a used model cannot strand existing cell coordinates.
        """
        response = self.client.patch(
            f'/seedtrays/seedtraymodels/{self.tray_model.pk}/',
            data=json.dumps({'x_cells': 1}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.tray_model.refresh_from_db()
        self.assertEqual(self.tray_model.x_cells, 2)

    def test_existing_tray_cannot_change_model(self):
        """
        A tray cannot swap to a model whose dimensions differ from its generated grid.
        """
        response = self.client.patch(
            f'/seedtrays/seedtrays/{self.tray.pk}/',
            data=json.dumps({'model': self.other_model.pk}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.tray.refresh_from_db()
        self.assertEqual(self.tray.model, self.tray_model)
