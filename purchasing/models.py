"""Workspace-scoped purchasing, payable, and business-expense records.

Stock receipts remain the authority for physical inventory and its valuation.
These records describe commercial commitments and liabilities around that stock;
matching an invoice never silently rewrites a posted lot's cost.
"""

# pylint: disable=duplicate-code,too-many-lines

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from inventory.models import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    POSITIVE_DECIMAL,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
)
from inventory.units import UnitCode
from workspaces.models import WorkspaceOwnedModel


ZERO = Decimal('0.0000')


def operation_key():
    """Return an opaque idempotency identity for an immutable financial act."""
    return uuid4()


class ValidatedModel(models.Model):
    """Validate ordinary ORM writes, matching the repository's domain models."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AppendOnlyModel(ValidatedModel):
    """A record corrected by a linked compensating record, never by mutation."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(f'{type(self).__name__} records are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f'{type(self).__name__} records cannot be deleted.')


class PurchaseRequisition(WorkspaceOwnedModel, ValidatedModel):
    """A reviewed need which may become one purchase-order line."""

    class Status(models.TextChoices):
        """Review and conversion lifecycle."""

        DRAFT = 'draft', 'Draft'
        REVIEWED = 'reviewed', 'Reviewed'
        ORDERED = 'ordered', 'Ordered'
        CANCELLED = 'cancelled', 'Cancelled'

    item = models.ForeignKey(
        'inventory.InventoryItem', on_delete=models.PROTECT,
        related_name='purchase_requisitions',
    )
    source_issue = models.ForeignKey(
        'plantings.NurseryPlanIssue', on_delete=models.PROTECT,
        null=True, blank=True, related_name='purchase_requisitions',
    )
    required_on = models.DateField()
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    unit_code = models.CharField(max_length=16, choices=UnitCode.choices)
    preferred_supplier = models.ForeignKey(
        'supplies.Supplier', on_delete=models.PROTECT, null=True, blank=True,
        related_name='purchase_requisitions',
    )
    estimated_total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, validators=[MinValueValidator(ZERO)],
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT,
        editable=False,
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['required_on', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='purchasing_requisition_quantity_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(estimated_total_incl_tax__gte=0),
                name='purchasing_requisition_estimate_nonnegative',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ('item', 'preferred_supplier'):
            value = getattr(self, field, None)
            if value is not None and value.workspace_id != self.workspace_id:
                errors[field] = f'The {field.replace("_", " ")} belongs to another workspace.'
        if self.source_issue_id and self.source_issue.plan.workspace_id != self.workspace_id:
            errors['source_issue'] = 'The planning issue belongs to another workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Reviewed requisitions are immutable.')
        super().save(*args, **kwargs)


class PurchaseOrder(WorkspaceOwnedModel, ValidatedModel):
    """A supplier commitment editable as a draft and frozen on confirmation."""

    class Status(models.TextChoices):
        """Supplier commitment lifecycle."""

        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        CLOSED = 'closed', 'Closed'
        CANCELLED = 'cancelled', 'Cancelled'

    order_number = models.CharField(max_length=64)
    supplier = models.ForeignKey(
        'supplies.Supplier', on_delete=models.PROTECT, related_name='purchase_orders',
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT,
        editable=False,
    )
    ordered_on = models.DateField()
    expected_on = models.DateField(null=True, blank=True)
    currency_code = models.CharField(
        max_length=3,
        validators=[RegexValidator(r'^[A-Z]{3}$', 'Enter a three-letter uppercase ISO 4217 currency code.')],
    )
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    freight_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ordered_on', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'order_number'], name='purchasing_order_number_unique',
        )]

    def clean(self):
        super().clean()
        if self.supplier_id and self.supplier.workspace_id != self.workspace_id:
            raise ValidationError({'supplier': 'The supplier belongs to another workspace.'})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Confirmed purchase orders are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Only draft purchase orders can be deleted.')
        return super().delete(*args, **kwargs)


class PurchaseOrderLine(ValidatedModel):
    """One physical item and its frozen commercial terms."""

    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='lines',
    )
    item = models.ForeignKey(
        'inventory.InventoryItem', on_delete=models.PROTECT,
        related_name='purchase_order_lines',
    )
    requisition = models.OneToOneField(
        PurchaseRequisition, on_delete=models.PROTECT, null=True, blank=True,
        related_name='order_line',
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    unit_code = models.CharField(max_length=16, choices=UnitCode.choices)
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    unit_price_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(ZERO)],
    )
    tax_rate = models.DecimalField(
        max_digits=7, decimal_places=4, default=ZERO,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
    )
    freight_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, validators=[MinValueValidator(ZERO)],
    )
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO, editable=False,
    )
    cancelled_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        default=Decimal('0'), editable=False,
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='purchasing_order_line_quantity_positive'),
            models.CheckConstraint(condition=models.Q(base_quantity__gt=0), name='purchasing_order_line_base_quantity_positive'),
            models.CheckConstraint(condition=models.Q(cancelled_quantity__gte=0), name='purchasing_order_line_cancelled_nonnegative'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.order_id and self.item_id and self.order.workspace_id != self.item.workspace_id:
            errors['item'] = 'The item belongs to another workspace.'
        if self.requisition_id:
            if self.requisition.workspace_id != self.order.workspace_id:
                errors['requisition'] = 'The requisition belongs to another workspace.'
            elif self.requisition.item_id != self.item_id:
                errors['requisition'] = 'The requisition is for another item.'
            elif self.requisition.status != PurchaseRequisition.Status.REVIEWED:
                errors['requisition'] = 'Only a reviewed requisition can be ordered.'
        if self.cancelled_quantity > self.base_quantity:
            errors['cancelled_quantity'] = 'Cancelled quantity cannot exceed ordered quantity.'
        if errors:
            raise ValidationError(errors)

        if self.order_id and self.order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError({'order': 'Lines can only be added to a draft order.'})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.select_related('order').get(pk=self.pk)
            if previous.order.status != PurchaseOrder.Status.DRAFT:
                raise ValidationError('Confirmed purchase-order lines are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError('Confirmed purchase-order lines cannot be deleted.')
        return super().delete(*args, **kwargs)


class PurchaseOrderCancellation(AppendOnlyModel):
    """An auditable cancellation of some or all outstanding ordered stock."""

    line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name='cancellations',
    )
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [models.CheckConstraint(
            condition=models.Q(base_quantity__gt=0),
            name='purchasing_order_cancel_quantity_positive',
        )]


class ReceiptMatch(AppendOnlyModel):
    """A quantity on a posted stock receipt reconciled to an ordered line."""

    order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name='receipt_matches',
    )
    receipt_line = models.ForeignKey(
        'inventory.StockReceiptLine', on_delete=models.PROTECT,
        related_name='purchase_order_matches',
    )
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [models.CheckConstraint(
            condition=models.Q(base_quantity__gt=0),
            name='purchasing_receipt_match_quantity_positive',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.order_line_id and self.receipt_line_id:
            if self.order_line.order.workspace_id != self.receipt_line.receipt.workspace_id:
                errors['receipt_line'] = 'The receipt belongs to another workspace.'
            elif self.order_line.item_id != self.receipt_line.item_id:
                errors['receipt_line'] = 'The receipt line contains another item.'
            elif self.order_line.order.supplier_id != self.receipt_line.receipt.supplier_id:
                errors['receipt_line'] = 'The receipt is from another supplier.'
            elif self.receipt_line.receipt.status != 'posted':
                errors['receipt_line'] = 'Only a posted receipt can be matched.'
        if errors:
            raise ValidationError(errors)


class ExpenseCategory(WorkspaceOwnedModel, ValidatedModel):
    """A reusable non-stock business-expense classification."""

    name = models.CharField(max_length=128)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'name'], name='purchasing_expense_category_unique',
        )]

    def __str__(self):
        return self.name


class SupplierInvoice(WorkspaceOwnedModel, ValidatedModel):
    """A supplier liability, frozen when confirmed and corrected explicitly."""

    class Status(models.TextChoices):
        """Payable document lifecycle."""

        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    supplier = models.ForeignKey(
        'supplies.Supplier', on_delete=models.PROTECT, related_name='supplier_invoices',
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True,
        related_name='invoices',
    )
    external_reference = models.CharField(max_length=255)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    currency_code = models.CharField(
        max_length=3,
        validators=[RegexValidator(r'^[A-Z]{3}$', 'Enter a three-letter uppercase ISO 4217 currency code.')],
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False,
    )
    supplier_name_snapshot = models.CharField(max_length=1024, blank=True, default='')
    supplier_address_snapshot = models.TextField(blank=True, default='')
    supplier_gst_number_snapshot = models.CharField(max_length=16, blank=True, default='')
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO, editable=False)
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO, editable=False)
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO, editable=False)
    attachment_url = models.URLField(max_length=2048, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-invoice_date', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'supplier', 'external_reference'],
            name='purchasing_supplier_invoice_reference_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.supplier_id and self.supplier.workspace_id != self.workspace_id:
            errors['supplier'] = 'The supplier belongs to another workspace.'
        if self.purchase_order_id:
            if self.purchase_order.workspace_id != self.workspace_id:
                errors['purchase_order'] = 'The purchase order belongs to another workspace.'
            elif self.purchase_order.supplier_id != self.supplier_id:
                errors['purchase_order'] = 'The purchase order is for another supplier.'
        if self.due_date and self.due_date < self.invoice_date:
            errors['due_date'] = 'The due date cannot precede the invoice date.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Confirmed supplier invoices are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Confirmed supplier invoices cannot be deleted.')
        return super().delete(*args, **kwargs)


class SupplierInvoiceLine(ValidatedModel):
    """One payable amount, optionally matched to ordered or received stock."""

    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name='lines')
    description = models.CharField(max_length=255)
    purchase_order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, null=True, blank=True,
        related_name='invoice_lines',
    )
    receipt_line = models.ForeignKey(
        'inventory.StockReceiptLine', on_delete=models.PROTECT, null=True, blank=True,
        related_name='supplier_invoice_lines',
    )
    expense_category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name='supplier_invoice_lines',
    )
    is_freight = models.BooleanField(default=False)
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO)])
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=ZERO, validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))])
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO)])
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO)])

    class Meta:
        ordering = ['pk']

    def clean(self):
        super().clean()
        errors = {}
        targets = sum(value is not None for value in (
            self.purchase_order_line_id, self.receipt_line_id, self.expense_category_id,
        )) + int(self.is_freight)
        if targets > 1:
            errors['target'] = 'Choose at most one stock, freight, or expense target.'
        workspace_id = self.invoice.workspace_id if self.invoice_id else None
        if self.purchase_order_line_id and self.purchase_order_line.order.workspace_id != workspace_id:
            errors['purchase_order_line'] = 'The order line belongs to another workspace.'
        elif self.purchase_order_line_id and self.purchase_order_line.order.supplier_id != self.invoice.supplier_id:
            errors['purchase_order_line'] = 'The order line belongs to another supplier.'
        if self.receipt_line_id and self.receipt_line.receipt.workspace_id != workspace_id:
            errors['receipt_line'] = 'The receipt line belongs to another workspace.'
        elif self.receipt_line_id and self.receipt_line.receipt.supplier_id != self.invoice.supplier_id:
            errors['receipt_line'] = 'The receipt line belongs to another supplier.'
        elif self.receipt_line_id and self.receipt_line.receipt.status != 'posted':
            errors['receipt_line'] = 'Only a posted receipt can be invoiced.'
        if self.expense_category_id and self.expense_category.workspace_id != workspace_id:
            errors['expense_category'] = 'The category belongs to another workspace.'
        if self.total_incl_tax != self.subtotal_ex_tax + self.tax_total:
            errors['total_incl_tax'] = 'The line total must equal subtotal plus tax.'
        if self.invoice_id and self.invoice.status != SupplierInvoice.Status.DRAFT:
            errors['invoice'] = 'Lines can only be added to a draft invoice.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.select_related('invoice').get(pk=self.pk)
            if previous.invoice.status != SupplierInvoice.Status.DRAFT:
                raise ValidationError('Confirmed supplier-invoice lines are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != SupplierInvoice.Status.DRAFT:
            raise ValidationError('Confirmed supplier-invoice lines cannot be deleted.')
        return super().delete(*args, **kwargs)


class SupplierInvoiceCorrection(WorkspaceOwnedModel, AppendOnlyModel):
    """An append-only credit or debit correcting a confirmed invoice."""

    class Kind(models.TextChoices):
        """Directions in which an invoice may be corrected."""

        CREDIT = 'credit', 'Credit'
        DEBIT = 'debit', 'Debit'

    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.PROTECT, related_name='corrections',
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    external_reference = models.CharField(max_length=255)
    corrected_on = models.DateField()
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(POSITIVE_DECIMAL)])
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO, validators=[MinValueValidator(ZERO)])
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(POSITIVE_DECIMAL)])
    reason = models.TextField()
    attachment_url = models.URLField(max_length=2048, blank=True, default='')
    operation_key = models.UUIDField(default=operation_key)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['corrected_on', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'operation_key'], name='purchasing_invoice_correction_operation_unique'),
            models.UniqueConstraint(fields=['workspace', 'external_reference'], name='purchasing_invoice_correction_reference_unique'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.invoice_id and self.invoice.workspace_id != self.workspace_id:
            errors['invoice'] = 'The invoice belongs to another workspace.'
        if self.invoice_id and self.invoice.status != SupplierInvoice.Status.CONFIRMED:
            errors['invoice'] = 'Only a confirmed invoice can be corrected.'
        if self.invoice_id and self.corrected_on < self.invoice.invoice_date:
            errors['corrected_on'] = 'The correction cannot predate the invoice.'
        if self.total_incl_tax != self.subtotal_ex_tax + self.tax_total:
            errors['total_incl_tax'] = 'The correction total must equal subtotal plus tax.'
        if errors:
            raise ValidationError(errors)


class SupplierPayment(WorkspaceOwnedModel, AppendOnlyModel):
    """Money paid to a supplier, allocated across one or more invoices."""

    class Method(models.TextChoices):
        """Operational descriptions of payment tender."""

        CASH = 'cash', 'Cash'
        CARD = 'card', 'Card'
        BANK_TRANSFER = 'bank_transfer', 'Bank transfer'
        OTHER = 'other', 'Other'

    supplier = models.ForeignKey(
        'supplies.Supplier', on_delete=models.PROTECT, related_name='supplier_payments',
    )
    paid_on = models.DateField()
    amount = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(POSITIVE_DECIMAL)])
    currency_code = models.CharField(max_length=3)
    method = models.CharField(max_length=16, choices=Method.choices)
    external_reference = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    reversal_of = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='reversal')
    operation_key = models.UUIDField(default=operation_key)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['paid_on', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'operation_key'], name='purchasing_supplier_payment_operation_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.supplier_id and self.supplier.workspace_id != self.workspace_id:
            errors['supplier'] = 'The supplier belongs to another workspace.'
        if self.reversal_of_id:
            if self.reversal_of.workspace_id != self.workspace_id:
                errors['reversal_of'] = 'The payment belongs to another workspace.'
            elif self.reversal_of.reversal_of_id:
                errors['reversal_of'] = 'A reversal cannot itself be reversed.'
            elif self.reversal_of.supplier_id != self.supplier_id:
                errors['supplier'] = 'A reversal must use the original supplier.'
        if errors:
            raise ValidationError(errors)


class SupplierPaymentAllocation(AppendOnlyModel):
    """The portion of one immutable payment applied to one invoice."""

    payment = models.ForeignKey(SupplierPayment, on_delete=models.PROTECT, related_name='allocations')
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name='payment_allocations')
    amount = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(POSITIVE_DECIMAL)])

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['payment', 'invoice'], name='purchasing_payment_invoice_unique'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='purchasing_payment_allocation_positive'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.payment_id and self.invoice_id:
            if self.payment.workspace_id != self.invoice.workspace_id:
                errors['invoice'] = 'The invoice belongs to another workspace.'
            elif self.payment.supplier_id != self.invoice.supplier_id:
                errors['invoice'] = 'The invoice belongs to another supplier.'
            elif self.invoice.status != SupplierInvoice.Status.CONFIRMED:
                errors['invoice'] = 'Only a confirmed invoice can be paid.'
        if errors:
            raise ValidationError(errors)


class BusinessExpense(WorkspaceOwnedModel, ValidatedModel):
    """A confirmed non-stock business cost, optionally allocated operationally."""

    class Status(models.TextChoices):
        """Expense review lifecycle."""

        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    supplier = models.ForeignKey('supplies.Supplier', on_delete=models.PROTECT, null=True, blank=True, related_name='business_expenses')
    payee = models.CharField(max_length=255, blank=True, default='')
    incurred_on = models.DateField()
    currency_code = models.CharField(max_length=3)
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO)])
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO, validators=[MinValueValidator(ZERO)])
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO)])
    supplier_invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, null=True, blank=True, related_name='business_expenses')
    paid_on = models.DateField(null=True, blank=True)
    garden_area = models.ForeignKey('garden.GardenArea', on_delete=models.PROTECT, null=True, blank=True, related_name='business_expenses')
    crop_plan = models.ForeignKey('plantings.NurseryProductionPlan', on_delete=models.PROTECT, null=True, blank=True, related_name='business_expenses')
    production_batch = models.ForeignKey('plantings.ProductionBatch', on_delete=models.PROTECT, null=True, blank=True, related_name='business_expenses')
    allocation_type = models.CharField(max_length=32, blank=True, default='', help_text='Future allocation kind such as equipment, channel, market, or delivery.')
    allocation_reference = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False)
    attachment_url = models.URLField(max_length=2048, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-incurred_on', '-pk']

    def clean(self):
        super().clean()
        errors = {}
        for field in ('category', 'supplier', 'garden_area', 'crop_plan', 'production_batch', 'supplier_invoice'):
            value = getattr(self, field, None)
            if value is not None and value.workspace_id != self.workspace_id:
                errors[field] = f'The {field.replace("_", " ")} belongs to another workspace.'
        if not self.supplier_id and not self.payee.strip():
            errors['payee'] = 'Name a supplier or payee.'
        if self.total_incl_tax != self.subtotal_ex_tax + self.tax_total:
            errors['total_incl_tax'] = 'The expense total must equal subtotal plus tax.'
        if self.supplier_invoice_id and self.paid_on:
            errors['paid_on'] = 'Payment is derived from the linked supplier invoice.'
        if bool(self.allocation_type) != bool(self.allocation_reference):
            errors['allocation_reference'] = 'Provide both a future allocation type and reference.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Confirmed business expenses are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Confirmed business expenses cannot be deleted.')
        return super().delete(*args, **kwargs)
