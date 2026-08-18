"""
Tests for Gardens
"""
import json
from importlib import import_module
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from tests.api import RESTContractTestCase
from tests.factories import make_garden_area, make_garden_bed

from .models import GardenArea, GardenBed, GardenRow, GardenSquare


class GardenRESTContractTests(RESTContractTestCase):
    """Smoke tests for the garden REST resources."""

    LIST_URLS = (
        '/garden/areas/',
        '/garden/beds/',
        '/garden/rows/',
        '/garden/squares/',
    )

    def setUp(self):
        super().setUp()
        self.area = make_garden_area()
        self.bed = make_garden_bed(area=self.area)

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list garden resources."""
        self.assert_authentication_required(self.LIST_URLS)

    def test_list_routes_return_lists(self):
        """Authenticated garden collections use the common list contract."""
        self.assert_list_contract(self.LIST_URLS)

    def test_resources_round_trip(self):
        """Each garden resource can be created and retrieved."""
        area = self.assert_create_retrieve(
            '/garden/areas/',
            {
                'name': 'Kitchen garden',
                'size_x': 80,
                'size_y': 60,
            },
        )
        bed = self.assert_create_retrieve(
            '/garden/beds/',
            {
                'area': area['pk'],
                'name': 'North bed',
                'placement_x': 2,
                'placement_y': 3,
                'size_x': 20,
                'size_y': 10,
            },
        )
        child_geometry = {
            'bed': bed['pk'],
            'placement_x': 1,
            'placement_y': 1,
            'size_x': 4,
            'size_y': 1,
        }
        self.assert_create_retrieve(
            '/garden/rows/',
            {**child_geometry, 'name': 'Carrot row'},
        )
        self.assert_create_retrieve(
            '/garden/squares/',
            {**child_geometry, 'name': 'Square A1'},
        )


class GardenGeometryAPITests(TestCase):
    """Garden APIs enforce drawable sizes and parent-relative placements."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='geometry-tester'))
        self.area = GardenArea.objects.create(
            name='Geometry area',
            size_x=100,
            size_y=100,
        )
        # Placed away from the origin so the payloads below can exercise a
        # zero placement without colliding with it.
        self.bed = GardenBed.objects.create(
            area=self.area,
            name='Geometry bed',
            placement_x=50,
            placement_y=50,
            size_x=50,
            size_y=50,
        )

    def _geometry_endpoints(self):
        return [
            (
                '/garden/areas/',
                GardenArea,
                {'name': 'New area', 'size_x': 10, 'size_y': 10},
            ),
            (
                '/garden/beds/',
                GardenBed,
                {
                    'area': self.area.pk,
                    'name': 'New bed',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 10,
                    'size_y': 10,
                },
            ),
            (
                '/garden/rows/',
                GardenRow,
                {
                    'bed': self.bed.pk,
                    'name': 'New row',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 10,
                    'size_y': 1,
                },
            ),
            (
                '/garden/squares/',
                GardenSquare,
                {
                    'bed': self.bed.pk,
                    'name': 'New square',
                    'placement_x': 0,
                    'placement_y': 0,
                    'size_x': 1,
                    'size_y': 1,
                },
            ),
        ]

    def test_create_rejects_non_positive_sizes(self):
        """Every drawable object must have positive width and height."""
        for url, model, payload in self._geometry_endpoints():
            for field in ('size_x', 'size_y'):
                for value in (0, -1):
                    with self.subTest(model=model.__name__, field=field, value=value):
                        original_count = model.objects.count()
                        response = self.client.post(
                            url,
                            data=json.dumps({**payload, field: value}),
                            content_type='application/json',
                        )

                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(
                            response.json(),
                            {field: ['Ensure this value is greater than or equal to 1.']},
                        )
                        self.assertEqual(model.objects.count(), original_count)

    def test_create_rejects_negative_placements(self):
        """Child geometry cannot begin outside its parent's zero origin."""
        for url, model, payload in self._geometry_endpoints()[1:]:
            for field in ('placement_x', 'placement_y'):
                with self.subTest(model=model.__name__, field=field):
                    original_count = model.objects.count()
                    response = self.client.post(
                        url,
                        data=json.dumps({**payload, field: -1}),
                        content_type='application/json',
                    )

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.json(),
                        {field: ['Ensure this value is greater than or equal to 0.']},
                    )
                    self.assertEqual(model.objects.count(), original_count)

    def test_create_allows_zero_placements(self):
        """The parent's origin is a valid inclusive placement boundary."""
        for url, model, payload in self._geometry_endpoints()[1:]:
            with self.subTest(model=model.__name__):
                response = self.client.post(
                    url,
                    data=json.dumps(payload),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 201)
                created = model.objects.get(pk=response.json()['pk'])
                self.assertEqual(created.placement_x, 0)
                self.assertEqual(created.placement_y, 0)

    def test_database_rejects_non_positive_sizes(self):
        """Direct writes cannot bypass positive drawable dimensions."""
        geometry = [
            self.area,
            self.bed,
            GardenRow.objects.create(
                bed=self.bed,
                name='Constrained row',
                placement_x=0,
                placement_y=0,
                size_x=10,
                size_y=1,
            ),
            GardenSquare.objects.create(
                bed=self.bed,
                name='Constrained square',
                placement_x=0,
                placement_y=0,
                size_x=1,
                size_y=1,
            ),
        ]

        for instance in geometry:
            for field in ('size_x', 'size_y'):
                with self.subTest(model=type(instance).__name__, field=field):
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            type(instance).objects.filter(pk=instance.pk).update(**{field: 0})
                    instance.refresh_from_db()
                    self.assertGreaterEqual(getattr(instance, field), 1)

    def test_database_rejects_negative_placements(self):
        """Direct writes cannot move child geometry before the parent origin."""
        children = [
            self.bed,
            GardenRow.objects.create(
                bed=self.bed,
                name='Constrained row',
                placement_x=0,
                placement_y=0,
                size_x=10,
                size_y=1,
            ),
            GardenSquare.objects.create(
                bed=self.bed,
                name='Constrained square',
                placement_x=0,
                placement_y=0,
                size_x=1,
                size_y=1,
            ),
        ]

        for instance in children:
            for field in ('placement_x', 'placement_y'):
                with self.subTest(model=type(instance).__name__, field=field):
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            type(instance).objects.filter(pk=instance.pk).update(**{field: -1})
                    instance.refresh_from_db()
                    self.assertGreaterEqual(getattr(instance, field), 0)

    def test_geometry_audit_accepts_valid_rows(self):
        """Valid existing geometry does not block database constraints."""
        migration = import_module('garden.migrations.0004_constrain_garden_geometry')
        migration.audit_garden_geometry(django_apps, None)

    def test_geometry_audit_reports_model_field_and_row_ids(self):
        """Deployment failures identify invalid geometry precisely."""
        migration = import_module('garden.migrations.0004_constrain_garden_geometry')
        invalid_sizes = mock.MagicMock()
        invalid_sizes.count.return_value = 1
        size_values = invalid_sizes.order_by.return_value.values_list.return_value
        size_values.__getitem__.return_value = [9]

        invalid_placements = mock.MagicMock()
        invalid_placements.count.return_value = 1
        placement_values = (
            invalid_placements.order_by.return_value.values_list.return_value
        )
        placement_values.__getitem__.return_value = [10]

        empty_rows = mock.MagicMock()
        empty_rows.count.return_value = 0

        def model_returning(*rows):
            historical_model = mock.MagicMock()
            historical_model.objects.filter.side_effect = rows
            return historical_model

        historical_apps = mock.MagicMock()
        historical_apps.get_model.side_effect = [
            model_returning(invalid_sizes),
            model_returning(empty_rows, invalid_placements),
            model_returning(empty_rows, empty_rows),
            model_returning(empty_rows, empty_rows),
        ]

        with self.assertRaises(RuntimeError) as raised:
            migration.audit_garden_geometry(historical_apps, None)

        self.assertIn('GardenArea size IDs: [9]', str(raised.exception))
        self.assertIn('GardenBed placement IDs: [10]', str(raised.exception))
        invalid_sizes.count.assert_called_once_with()
        invalid_placements.count.assert_called_once_with()
