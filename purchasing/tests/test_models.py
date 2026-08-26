"""Structural contracts for purchasing and accounts payable."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import InventoryItem
from inventory.units import UnitCode
from supplies.models import Supplier
from workspaces.models import Workspace, get_current_workspace

from purchasing.models import (
    BusinessExpense,
    ExpenseCategory,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    SupplierInvoice,
    SupplierInvoiceCorrection,
    SupplierInvoiceLine,
    SupplierPayment,
    SupplierPaymentAllocation,
)


class PurchasingModelTests(TestCase):
    """Commercial and payable records retain ownership and audit history."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='purchasing-model-user')
        self.supplier = Supplier.objects.create(
            workspace=self.workspace, name='Seed merchant',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Carrot seed',
            category=InventoryItem.Category.SEED,
            base_unit=UnitCode.SEED,
        )
        self.category = ExpenseCategory.objects.create(
            workspace=self.workspace, name='Market fees',
        )

    def make_order(self):
        """Return a draft order with one normalized commercial line."""
        order = PurchaseOrder.objects.create(
            workspace=self.workspace,
            order_number='PO-1',
            supplier=self.supplier,
            ordered_on=date(2026, 8, 26),
            currency_code='NZD',
            created_by=self.user,
        )
        PurchaseOrderLine.objects.create(
            order=order,
            item=self.item,
            description='One packet',
            quantity=Decimal('100'),
            unit_code=UnitCode.SEED,
            base_quantity=Decimal('100'),
            unit_price_ex_tax=Decimal('0.10'),
            tax_rate=Decimal('15'),
        )
        return order

    def make_invoice(self):
        """Return a draft supplier invoice with one reconciled line."""
        order = self.make_order()
        invoice = SupplierInvoice.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            purchase_order=order,
            external_reference='SUP-100',
            invoice_date=date(2026, 8, 26),
            due_date=date(2026, 9, 20),
            currency_code='NZD',
            created_by=self.user,
        )
        SupplierInvoiceLine.objects.create(
            invoice=invoice,
            purchase_order_line=order.lines.get(),
            description='Carrot seed',
            subtotal_ex_tax=Decimal('10'),
            tax_rate=Decimal('15'),
            tax_total=Decimal('1.5'),
            total_incl_tax=Decimal('11.5'),
        )
        return invoice

    def test_requisition_keeps_need_distinct_from_order(self):
        """A reviewed need has its own supplier preference and estimate."""
        requisition = PurchaseRequisition.objects.create(
            workspace=self.workspace,
            item=self.item,
            required_on=date(2026, 9, 1),
            quantity=Decimal('200'),
            unit_code=UnitCode.SEED,
            preferred_supplier=self.supplier,
            estimated_total_incl_tax=Decimal('23'),
            created_by=self.user,
        )
        self.assertEqual(requisition.status, PurchaseRequisition.Status.DRAFT)
        self.assertFalse(hasattr(requisition, 'order_line'))

    def test_confirmed_order_and_lines_are_immutable(self):
        """Commercial terms cannot be rewritten after confirmation."""
        order = self.make_order()
        PurchaseOrder.objects.filter(pk=order.pk).update(
            status=PurchaseOrder.Status.CONFIRMED,
        )
        order.refresh_from_db()
        order.notes = 'Changed after sending'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            order.save()
        line = order.lines.get()
        line.quantity = Decimal('50')
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            line.save()

    def test_invoice_target_is_exclusive_and_money_reconciles(self):
        """A payable line cannot be both stock and an expense or fail to add up."""
        invoice = self.make_invoice()
        line = invoice.lines.get()
        line.expense_category = self.category
        with self.assertRaises(ValidationError):
            line.save()
        line.expense_category = None
        line.total_incl_tax = Decimal('12')
        with self.assertRaisesMessage(ValidationError, 'subtotal plus tax'):
            line.save()

    def test_confirmed_invoice_is_corrected_append_only(self):
        """A credit remains linked without changing the original invoice."""
        invoice = self.make_invoice()
        SupplierInvoice.objects.filter(pk=invoice.pk).update(
            status=SupplierInvoice.Status.CONFIRMED,
            subtotal_ex_tax=Decimal('10'),
            tax_total=Decimal('1.5'),
            total_incl_tax=Decimal('11.5'),
        )
        invoice.refresh_from_db()
        original_reference = invoice.external_reference
        invoice.external_reference = 'REWRITTEN'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            invoice.save()
        correction = SupplierInvoiceCorrection.objects.create(
            workspace=self.workspace,
            invoice=invoice,
            kind=SupplierInvoiceCorrection.Kind.CREDIT,
            external_reference='CR-100',
            corrected_on=date(2026, 8, 27),
            subtotal_ex_tax=Decimal('2'),
            tax_total=Decimal('0.3'),
            total_incl_tax=Decimal('2.3'),
            reason='One packet damaged',
            created_by=self.user,
        )
        correction.refresh_from_db()
        self.assertEqual(correction.invoice.external_reference, original_reference)
        correction.reason = 'Rewritten reason'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            correction.save()

    def test_payment_allocation_is_supplier_scoped_and_append_only(self):
        """Money can only discharge confirmed invoices for its supplier."""
        invoice = self.make_invoice()
        SupplierInvoice.objects.filter(pk=invoice.pk).update(
            status=SupplierInvoice.Status.CONFIRMED,
        )
        invoice.refresh_from_db()
        payment = SupplierPayment.objects.create(
            workspace=self.workspace,
            supplier=self.supplier,
            paid_on=date(2026, 8, 27),
            amount=Decimal('5'),
            currency_code='NZD',
            method=SupplierPayment.Method.BANK_TRANSFER,
            created_by=self.user,
        )
        allocation = SupplierPaymentAllocation.objects.create(
            payment=payment, invoice=invoice, amount=Decimal('5'),
        )
        allocation.amount = Decimal('4')
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            allocation.save()

    def test_expense_requires_payee_and_operational_links_stay_in_workspace(self):
        """Whole-business expenses are valid without pretending to be stock."""
        expense = BusinessExpense(
            workspace=self.workspace,
            category=self.category,
            incurred_on=date(2026, 8, 26),
            currency_code='NZD',
            subtotal_ex_tax=Decimal('20'),
            tax_total=Decimal('3'),
            total_incl_tax=Decimal('23'),
            created_by=self.user,
        )
        with self.assertRaisesMessage(ValidationError, 'supplier or payee'):
            expense.save()
        expense.payee = 'Town market'
        expense.save()
        self.assertEqual(expense.status, BusinessExpense.Status.DRAFT)

    def test_cross_workspace_supplier_is_refused(self):
        """A foreign supplier cannot leak into a local payable."""
        other = Workspace.objects.create(name='Other purchasing workspace')
        foreign_supplier = Supplier.objects.create(workspace=other, name='Foreign')
        with self.assertRaises(ValidationError):
            PurchaseOrder.objects.create(
                workspace=self.workspace,
                order_number='PO-FOREIGN',
                supplier=foreign_supplier,
                ordered_on=date(2026, 8, 26),
                currency_code='NZD',
            )
