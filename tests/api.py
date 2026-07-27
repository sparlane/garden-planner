"""Helpers for exercising the common REST resource contract."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase


class RESTContractTestCase(APITestCase):
    """Base class for authenticated REST contract tests."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username=f'{self.__class__.__name__}-user',
        )
        self.client.force_authenticate(self.user)

    def assert_authentication_required(self, urls):
        """Assert every list route rejects an anonymous request."""
        self.client.force_authenticate(user=None)
        try:
            for url in urls:
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 403)
        finally:
            self.client.force_authenticate(self.user)

    def assert_list_contract(self, urls):
        """Assert every authenticated list route returns an unpaginated list."""
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.data, list)

    def assert_create_retrieve(self, url, payload, expected_fields=None):
        """Create a resource and verify its fields through the detail route."""
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created_pk = response.data['pk']

        response = self.client.get(f'{url}{created_pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['pk'], created_pk)
        for field, expected in (expected_fields or payload).items():
            self.assertEqual(response.data[field], expected)
        return response.data
