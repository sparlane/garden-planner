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

from .models import GardenArea, GardenBed, GardenRow, GardenSquare


class GardenGeometryAPITests(TestCase):
    """Garden APIs enforce drawable sizes and parent-relative placements."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='geometry-tester'))
        self.area = GardenArea.objects.create(
            name='Geometry area',
            size_x=100,
            size_y=100,
        )
        self.bed = GardenBed.objects.create(
            area=self.area,
            name='Geometry bed',
            placement_x=0,
            placement_y=0,
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
        invalid_rows = mock.MagicMock()
        invalid_rows.count.return_value = 1
        values = invalid_rows.order_by.return_value.values_list.return_value
        values.__getitem__.return_value = [9]

        empty_rows = mock.MagicMock()
        empty_rows.count.return_value = 0

        def model_returning(rows):
            historical_model = mock.MagicMock()
            historical_model.objects.filter.return_value = rows
            return historical_model

        historical_apps = mock.MagicMock()
        historical_apps.get_model.side_effect = [
            model_returning(invalid_rows),
            model_returning(empty_rows),
            model_returning(empty_rows),
            model_returning(empty_rows),
        ]

        with self.assertRaisesMessage(RuntimeError, 'GardenArea size IDs: [9]'):
            migration.audit_garden_geometry(historical_apps, None)
