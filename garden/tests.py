"""
Tests for Gardens
"""
import json

from django.contrib.auth import get_user_model
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
