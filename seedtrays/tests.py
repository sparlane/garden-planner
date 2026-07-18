"""
Tests for seed trays
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import SeedTray, SeedTrayCell, SeedTrayModel


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
        self.tray = SeedTray.objects.create(model=self.tray_model)
        self.other_tray = SeedTray.objects.create(model=self.other_model)

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
