"""Tests for workspace configuration and selection."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from seedtrays.models import SeedTrayModel
from supplies.models import Supplier

from .admin import WorkspaceAdmin
from .current import get_current_workspace
from .models import Workspace


class CurrentWorkspaceTests(TestCase):
    """The configured ID selects one workspace deterministically."""

    def test_selects_configured_workspace_when_another_exists(self):
        """Other rows do not change the configured deployment workspace."""
        current = Workspace.objects.get(pk=1)
        Workspace.objects.create(name='Other workspace')

        self.assertEqual(get_current_workspace(), current)

    @override_settings(CURRENT_WORKSPACE_ID=9999)
    def test_missing_configured_workspace_fails_clearly(self):
        """A stale configured ID fails instead of leaking another workspace."""
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            'CURRENT_WORKSPACE_ID=9999 does not identify a workspace.',
        ):
            get_current_workspace()


class WorkspaceAdminTests(TestCase):
    """Admin cannot provision additional workspace rows."""

    def test_admin_never_provisions_additional_workspaces(self):
        """Concurrent admin requests cannot race to create workspace rows."""
        workspace_admin = WorkspaceAdmin(Workspace, AdminSite())

        self.assertFalse(workspace_admin.has_add_permission(request=None))


class WorkspaceOwnershipModelTests(TestCase):
    """Domain models receive a required workspace ownership root."""

    def test_direct_orm_creates_default_to_current_workspace(self):
        """Legacy and maintenance code receives the configured workspace."""
        supplier = Supplier.objects.create(name='Defaulted supplier')

        self.assertEqual(supplier.workspace, get_current_workspace())

    def test_tray_model_identifier_is_unique_within_workspace(self):
        """Identifiers can repeat across workspaces but not within one."""
        current = get_current_workspace()
        other = Workspace.objects.create(name='Other workspace')
        values = {
            'identifier': 'Shared identifier',
            'height': 10,
            'x_size': 20,
            'y_size': 20,
            'x_cells': 2,
            'y_cells': 2,
            'cell_size_ml': 40,
        }
        SeedTrayModel.objects.create(workspace=current, **values)
        SeedTrayModel.objects.create(workspace=other, **values)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SeedTrayModel.objects.create(workspace=current, **values)


class WorkspaceEndpointTests(APITestCase):
    """The singleton endpoint exposes and edits only profile settings."""

    url = '/settings/workspace/'

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='workspace-user')
        self.client.force_authenticate(self.user)

    def test_authentication_is_required(self):
        """Anonymous callers cannot read deployment profile settings."""
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_get_returns_neutral_defaults_without_an_id(self):
        """The profile response uses neutral migration defaults and hides IDs."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('pk', response.data)
        self.assertNotIn('id', response.data)
        self.assertEqual(response.data['name'], 'My Garden')
        self.assertEqual(response.data['mode'], 'garden')
        self.assertEqual(response.data['currency_code'], 'USD')
        self.assertEqual(response.data['default_tax_rate'], '0.0000')
        self.assertEqual(response.data['timezone'], 'UTC')
        self.assertEqual(response.data['measurement_system'], 'metric')

    def test_patch_updates_editable_settings(self):
        """Authenticated users share the ability to update the profile."""
        response = self.client.patch(
            self.url,
            {
                'name': 'Propagation House',
                'mode': 'nursery',
                'currency_code': 'NZD',
                'default_tax_rate': '15.0000',
                'timezone': 'Pacific/Auckland',
                'measurement_system': 'metric',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        workspace = Workspace.objects.get(pk=1)
        self.assertEqual(workspace.name, 'Propagation House')
        self.assertEqual(workspace.mode, Workspace.Mode.NURSERY)
        self.assertEqual(workspace.currency_code, 'NZD')
        self.assertEqual(workspace.default_tax_rate, Decimal('15'))
        self.assertEqual(workspace.timezone, 'Pacific/Auckland')

    def test_patch_validates_profile_fields(self):
        """Invalid profile values receive field-specific errors."""
        response = self.client.patch(
            self.url,
            {
                'mode': 'retail',
                'currency_code': 'nzd',
                'default_tax_rate': '101',
                'timezone': 'Middle/Earth',
                'measurement_system': 'mixed',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            set(response.data),
            {
                'mode',
                'currency_code',
                'default_tax_rate',
                'timezone',
                'measurement_system',
            },
        )

    def test_two_users_share_mode_changes_without_data_loss(self):
        """Profile switching is shared presentation state and retains records."""
        supplier = Supplier.objects.create(name='Persistent supplier')
        first_response = self.client.patch(
            self.url,
            {'mode': 'nursery'},
            format='json',
        )
        second_user = get_user_model().objects.create_user(
            username='second-workspace-user',
        )
        self.client.force_authenticate(second_user)
        shared_response = self.client.get(self.url)
        garden_response = self.client.patch(
            self.url,
            {'mode': 'garden'},
            format='json',
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(shared_response.data['mode'], 'nursery')
        self.assertEqual(garden_response.status_code, 200)
        self.assertTrue(Supplier.objects.filter(pk=supplier.pk).exists())

    def test_put_post_and_delete_are_not_supported(self):
        """The singleton resource supports partial updates only."""
        for method in ('put', 'post', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, {}, format='json')
                self.assertEqual(response.status_code, 405)
