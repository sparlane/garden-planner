"""A stand-in supplier for stock a Basic Garden workflow left unnamed."""

# pylint: disable=duplicate-code

from tests.api import RESTContractTestCase
from workspaces.current import get_current_workspace
from workspaces.models import Workspace

from .defaults import DEFAULT_SUPPLIER_NAME, ensure_default_supplier
from .models import Supplier


class DefaultSupplierTests(RESTContractTestCase):
    """One system-owned supplier absorbs an unnamed receipt."""

    def test_a_default_supplier_is_created(self):
        """A gardener who never names a supplier still gets a valid receipt."""
        supplier = ensure_default_supplier(get_current_workspace())
        self.assertEqual(supplier.name, DEFAULT_SUPPLIER_NAME)
        self.assertTrue(supplier.is_system_default)

    def test_calling_twice_returns_the_same_row(self):
        """Repeated Basic-mode receipts never create a second placeholder."""
        workspace = get_current_workspace()
        first = ensure_default_supplier(workspace)
        second = ensure_default_supplier(workspace)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Supplier.objects.filter(is_system_default=True).count(), 1)

    def test_a_renamed_default_is_left_alone(self):
        """An operator who renames it is not overwritten on the next call."""
        workspace = get_current_workspace()
        supplier = ensure_default_supplier(workspace)
        supplier.name = 'Gifts and swaps'
        supplier.save()
        ensure_default_supplier(workspace)
        supplier.refresh_from_db()
        self.assertEqual(supplier.name, 'Gifts and swaps')

    def test_each_workspace_gets_its_own(self):
        """The flag is unique per workspace, not across the deployment."""
        other = Workspace.objects.create(name='Other workspace')
        ensure_default_supplier(get_current_workspace())
        ensure_default_supplier(other)
        self.assertEqual(Supplier.objects.filter(is_system_default=True).count(), 2)
