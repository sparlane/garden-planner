"""Model and arithmetic contracts for customer sales."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from workspaces.models import get_current_workspace

from .models import Customer, SalesOrder, SalesOrderLine
from .services import create_order, update_pricing_mode


class SalesOrderArithmeticTests(TestCase):
    """Entered commercial terms produce stable canonical money snapshots."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='sales-model-user')

    def make_line(self, order, **overrides):
        """Build an arithmetic-only line with adjustable entered terms."""
        values = {
            'order': order,
            'line_type': SalesOrderLine.LineType.TRAY,
            'description': 'Two propagation trays',
            'quantity': 2,
            'unit_price': Decimal('11.5000'),
            'tax_rate': Decimal('15'),
            'discount_type': SalesOrderLine.DiscountType.FIXED,
            'discount_value': Decimal('3'),
        }
        # Arithmetic does not need a catalog target, so bypass only the target
        # constraint here by creating a seedling-shaped unsaved test double.
        line = SalesOrderLine(**values)
        for field, value in overrides.items():
            setattr(line, field, value)
        return line

    def test_order_creation_snapshots_workspace_defaults_and_sequences_numbers(self):
        """New documents freeze defaults and receive readable unique numbers."""
        self.workspace.sales_prices_include_tax = True
        self.workspace.save()
        first = create_order(self.workspace, self.user, status=SalesOrder.Status.QUOTE)
        second = create_order(self.workspace, self.user)
        self.assertEqual(first.order_number, 'SO-000001')
        self.assertEqual(second.order_number, 'SO-000002')
        self.assertTrue(first.prices_include_tax)
        self.assertEqual(first.currency_code, 'NZD')
        self.assertIsNotNone(first.quote_date)
        self.assertIsNotNone(second.order_date)

    def test_exclusive_and_inclusive_calculations_reconcile(self):
        """Both entry modes return canonical components that add exactly."""
        from .calculations import calculate_line  # pylint: disable=import-outside-toplevel

        exclusive = create_order(self.workspace, self.user)
        amounts = calculate_line(self.make_line(exclusive))
        self.assertEqual(amounts.subtotal_ex_tax, Decimal('20.0000'))
        self.assertEqual(amounts.tax_total, Decimal('3.0000'))
        self.assertEqual(amounts.total_incl_tax, Decimal('23.0000'))

        inclusive = create_order(self.workspace, self.user, prices_include_tax=True)
        amounts = calculate_line(self.make_line(inclusive))
        self.assertEqual(amounts.subtotal_ex_tax, Decimal('17.3913'))
        self.assertEqual(amounts.tax_total, Decimal('2.6087'))
        self.assertEqual(amounts.total_incl_tax, Decimal('20.0000'))
        self.assertEqual(amounts.subtotal_ex_tax + amounts.tax_total, amounts.total_incl_tax)

    def test_percentage_discount_uses_entered_gross(self):
        """Percentage discounts apply before tax in either entry mode."""
        from .calculations import calculate_line  # pylint: disable=import-outside-toplevel

        order = create_order(self.workspace, self.user)
        line = self.make_line(
            order,
            discount_type=SalesOrderLine.DiscountType.PERCENTAGE,
            discount_value=Decimal('10'),
        )
        amounts = calculate_line(line)
        self.assertEqual(amounts.discount_ex_tax, Decimal('2.3000'))
        self.assertEqual(amounts.total_incl_tax, Decimal('23.8050'))

    def test_customer_deactivates_instead_of_deleting(self):
        """Historical customer identities survive deactivation."""
        customer = Customer.objects.create(workspace=self.workspace, name='Local gardener')
        with self.assertRaisesMessage(ValidationError, 'deactivated'):
            customer.delete()
        customer.active = False
        customer.save()
        self.assertFalse(customer.active)

    def test_confirmed_pricing_mode_is_immutable(self):
        """A confirmed order cannot reinterpret its snapshotted terms."""
        order = create_order(self.workspace, self.user)
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        with self.assertRaises(ValidationError):
            update_pricing_mode(order, True)
