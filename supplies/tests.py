"""
Tests for supplies
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Supplier


class SupplierAPITests(TestCase):
    """Tests for the supplier REST resource."""

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_user(username='supplier-tester')
        )

    def test_create_and_retrieve_supplier(self):
        """An authenticated supplier write round-trips through the API."""
        response = self.client.post(
            '/supplies/supplier/',
            data=json.dumps({
                'name': 'Local Seed Company',
                'website': 'https://seeds.example.com',
                'notes': 'Open-pollinated varieties',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        supplier = Supplier.objects.get(pk=response.json()['pk'])

        response = self.client.get(f'/supplies/supplier/{supplier.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'pk': supplier.pk,
            'name': 'Local Seed Company',
            'website': 'https://seeds.example.com',
            'notes': 'Open-pollinated varieties',
        })
