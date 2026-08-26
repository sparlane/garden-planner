"""Transactional commands for purchasing and accounts payable."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import StockReceiptLine
from inventory.units import convert_standard_quantity

from .models import (
    BusinessExpense,
    PurchaseOrder,
    PurchaseOrderCancellation,
    PurchaseOrderLine,
    PurchaseRequisition,
    ReceiptMatch,
    SupplierInvoice,
    SupplierInvoiceCorrection,
    SupplierInvoiceLine,
    SupplierPayment,
    SupplierPaymentAllocation,
    ZERO,
)


MONEY_QUANTUM = Decimal('0.0001')


def money(value):
    """Canonicalize a financial input to ledger precision."""
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def order_line_amounts(quantity, unit_price_ex_tax, tax_rate, freight_ex_tax=ZERO):
    """Return stable exclusive, freight, tax, and inclusive line amounts."""
    merchandise = money(Decimal(quantity) * Decimal(unit_price_ex_tax))
    freight = money(freight_ex_tax)
    subtotal = money(merchandise + freight)
    tax = money(subtotal * Decimal(tax_rate) / Decimal('100'))
    return subtotal, freight, tax, money(subtotal + tax)


def normalize_order_quantity(item, quantity, unit_code):
    """Convert an entered controlled quantity into the item's base unit."""
    return convert_standard_quantity(Decimal(quantity), unit_code, item.base_unit)


def _line_values(values):
    item = values['item']
    quantity = values['quantity']
    unit_code = values['unit_code']
    subtotal, freight, tax, total = order_line_amounts(
        quantity,
        values['unit_price_ex_tax'],
        values.get('tax_rate', ZERO),
        values.get('freight_ex_tax', ZERO),
    )
    return {
        **values,
        'base_quantity': normalize_order_quantity(item, quantity, unit_code),
        'subtotal_ex_tax': subtotal,
        'freight_ex_tax': freight,
        'tax_total': tax,
        'total_incl_tax': total,
    }


def _refresh_order_totals(order):
    totals = order.lines.aggregate(
        subtotal=Sum('subtotal_ex_tax'),
        freight=Sum('freight_ex_tax'),
        tax=Sum('tax_total'),
        total=Sum('total_incl_tax'),
    )
    PurchaseOrder.objects.filter(pk=order.pk).update(
        subtotal_ex_tax=money(totals['subtotal'] or ZERO),
        freight_ex_tax=money(totals['freight'] or ZERO),
        tax_total=money(totals['tax'] or ZERO),
        total_incl_tax=money(totals['total'] or ZERO),
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def create_order(workspace, user, values, lines):
    """Create a draft purchase order and normalize every commercial line."""
    order = PurchaseOrder.objects.create(
        workspace=workspace, created_by=user, **values,
    )
    for values_for_line in lines:
        line = PurchaseOrderLine.objects.create(
            order=order, **_line_values(values_for_line),
        )
        if line.requisition_id:
            PurchaseRequisition.objects.filter(pk=line.requisition_id).update(
                status=PurchaseRequisition.Status.ORDERED,
            )
    return _refresh_order_totals(order)


@transaction.atomic
def replace_order_draft(order, values, lines):
    """Replace all editable terms on a locked draft order."""
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft purchase order can be edited.'})
    old_requisitions = list(order.lines.exclude(requisition=None).values_list('requisition_id', flat=True))
    PurchaseRequisition.objects.filter(
        pk__in=old_requisitions, status=PurchaseRequisition.Status.ORDERED,
    ).update(status=PurchaseRequisition.Status.REVIEWED)
    order.lines.all().delete()
    for field, value in values.items():
        setattr(order, field, value)
    order.save()
    for values_for_line in lines:
        line = PurchaseOrderLine.objects.create(
            order=order, **_line_values(values_for_line),
        )
        if line.requisition_id:
            PurchaseRequisition.objects.filter(pk=line.requisition_id).update(
                status=PurchaseRequisition.Status.ORDERED,
            )
    return _refresh_order_totals(order)


@transaction.atomic
def review_requisition(requisition, user):
    """Approve one draft need for conversion into an order."""
    del user
    requisition = PurchaseRequisition.objects.select_for_update().get(pk=requisition.pk)
    if requisition.status != PurchaseRequisition.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft requisition can be reviewed.'})
    PurchaseRequisition.objects.filter(pk=requisition.pk).update(
        status=PurchaseRequisition.Status.REVIEWED, reviewed_at=timezone.now(),
    )
    requisition.refresh_from_db()
    return requisition


@transaction.atomic
def cancel_requisition(requisition, user):
    """Cancel a need which has not yet become an order line."""
    del user
    requisition = PurchaseRequisition.objects.select_for_update().get(pk=requisition.pk)
    if requisition.status not in {
            PurchaseRequisition.Status.DRAFT,
            PurchaseRequisition.Status.REVIEWED}:
        raise ValidationError({'status': 'Only an unordered requisition can be cancelled.'})
    PurchaseRequisition.objects.filter(pk=requisition.pk).update(
        status=PurchaseRequisition.Status.CANCELLED, cancelled_at=timezone.now(),
    )
    requisition.refresh_from_db()
    return requisition


@transaction.atomic
def confirm_order(order, user):
    """Freeze a non-empty supplier order and its calculated totals."""
    del user
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft purchase order can be confirmed.'})
    if not order.lines.exists():
        raise ValidationError({'lines': 'Add at least one line before confirming.'})
    _refresh_order_totals(order)
    PurchaseOrder.objects.filter(pk=order.pk).update(
        status=PurchaseOrder.Status.CONFIRMED, confirmed_at=timezone.now(),
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def close_order(order, user):
    """Close a confirmed order whose lines have no outstanding quantity."""
    del user
    order = PurchaseOrder.objects.select_for_update().prefetch_related(
        'lines__receipt_matches', 'lines__cancellations',
    ).get(pk=order.pk)
    if order.status != PurchaseOrder.Status.CONFIRMED:
        raise ValidationError({'status': 'Only a confirmed purchase order can be closed.'})
    if any(order_line_state(line)['outstanding'] > 0 for line in order.lines.all()):
        raise ValidationError({'status': 'Receive or cancel every outstanding quantity first.'})
    PurchaseOrder.objects.filter(pk=order.pk).update(
        status=PurchaseOrder.Status.CLOSED, closed_at=timezone.now(),
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def cancel_order(order, reason, user):
    """Cancel every unreceived quantity and retain line-level history."""
    order = PurchaseOrder.objects.select_for_update().prefetch_related(
        'lines__receipt_matches', 'lines__cancellations',
    ).get(pk=order.pk)
    if order.status != PurchaseOrder.Status.CONFIRMED:
        raise ValidationError({'status': 'Only a confirmed purchase order can be cancelled.'})
    for line in order.lines.all():
        outstanding = order_line_state(line)['outstanding']
        if outstanding > 0:
            PurchaseOrderCancellation.objects.create(
                line=line, base_quantity=outstanding, reason=reason, created_by=user,
            )
            PurchaseOrderLine.objects.filter(pk=line.pk).update(
                cancelled_quantity=line.cancelled_quantity + outstanding,
            )
    PurchaseOrder.objects.filter(pk=order.pk).update(
        status=PurchaseOrder.Status.CANCELLED, cancelled_at=timezone.now(),
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def cancel_order_quantity(line, base_quantity, reason, user):
    """Append a quantity cancellation without rewriting confirmed terms."""
    line = PurchaseOrderLine.objects.select_for_update().select_related('order').get(pk=line.pk)
    if line.order.status != PurchaseOrder.Status.CONFIRMED:
        raise ValidationError({'order': 'Only a confirmed order can be cancelled.'})
    quantity = Decimal(base_quantity)
    already = line.cancellations.aggregate(total=Sum('base_quantity'))['total'] or Decimal('0')
    if quantity <= 0 or already + quantity > line.base_quantity:
        raise ValidationError({'base_quantity': 'Cancellation exceeds the uncancelled ordered quantity.'})
    cancellation = PurchaseOrderCancellation.objects.create(
        line=line, base_quantity=quantity, reason=reason, created_by=user,
    )
    PurchaseOrderLine.objects.filter(pk=line.pk).update(cancelled_quantity=already + quantity)
    return cancellation


@transaction.atomic
def match_receipt(order_line, receipt_line, base_quantity, user):
    """Match a posted delivery quantity while allowing visible over-delivery."""
    line = PurchaseOrderLine.objects.select_for_update().select_related('order').get(pk=order_line.pk)
    receipt = StockReceiptLine.objects.select_for_update().select_related('receipt', 'item').get(pk=receipt_line.pk)
    quantity = Decimal(base_quantity)
    matched = receipt.purchase_order_matches.aggregate(total=Sum('base_quantity'))['total'] or Decimal('0')
    if receipt.base_quantity is None:
        raise ValidationError({'receipt_line': 'An unknown receipt quantity cannot be matched.'})
    if quantity <= 0 or matched + quantity > receipt.base_quantity:
        raise ValidationError({'base_quantity': 'Match exceeds the receipt line quantity.'})
    return ReceiptMatch.objects.create(
        order_line=line, receipt_line=receipt,
        base_quantity=quantity, created_by=user,
    )


def order_line_state(line):
    """Return ordered, received, cancelled, returned, and outstanding quantities."""
    received = line.receipt_matches.aggregate(total=Sum('base_quantity'))['total'] or Decimal('0')
    returned = line.receipt_matches.filter(
        receipt_line__receipt__status='reversed',
    ).aggregate(total=Sum('base_quantity'))['total'] or Decimal('0')
    live_received = received - returned
    outstanding = line.base_quantity - line.cancelled_quantity - live_received
    return {
        'ordered': line.base_quantity,
        'received': live_received,
        'cancelled': line.cancelled_quantity,
        'returned': returned,
        'outstanding': max(outstanding, Decimal('0')),
        'over_received': max(-outstanding, Decimal('0')),
    }


def invoice_net_total(invoice):
    """Return invoice value after append-only credits and debits."""
    total = invoice.total_incl_tax
    for correction in invoice.corrections.all():
        direction = Decimal('-1') if correction.kind == SupplierInvoiceCorrection.Kind.CREDIT else Decimal('1')
        total += direction * correction.total_incl_tax
    return money(total)


def invoice_paid_total(invoice, through=None):
    """Return live allocated payments, optionally through a local date."""
    allocations = invoice.payment_allocations.select_related('payment').filter(
        payment__reversal_of=None,
        payment__reversal__isnull=True,
    )
    if through is not None:
        allocations = allocations.filter(payment__paid_on__lte=through)
    return money(allocations.aggregate(total=Sum('amount'))['total'] or ZERO)


def invoice_state(invoice):
    """Return corrected liability, payment state, and reconciliation warnings."""
    net = invoice_net_total(invoice)
    paid = invoice_paid_total(invoice)
    balance = money(max(net - paid, ZERO))
    warnings = []
    if invoice.status == SupplierInvoice.Status.CONFIRMED and not invoice.lines.exists():
        warnings.append('Invoice has no lines.')
    if invoice.lines.filter(
            purchase_order_line=None, receipt_line=None,
            expense_category=None, is_freight=False).exists():
        warnings.append('Invoice has unmatched lines.')
    if paid > net:
        warnings.append('Payments exceed the corrected invoice total.')
    return {
        'net_total': net,
        'paid_total': paid,
        'balance_due': balance,
        'payment_state': 'paid' if balance == 0 else ('part_paid' if paid > 0 else 'unpaid'),
        'warnings': warnings,
    }


def _refresh_invoice_totals(invoice):
    totals = invoice.lines.aggregate(
        subtotal=Sum('subtotal_ex_tax'), tax=Sum('tax_total'), total=Sum('total_incl_tax'),
    )
    SupplierInvoice.objects.filter(pk=invoice.pk).update(
        subtotal_ex_tax=money(totals['subtotal'] or ZERO),
        tax_total=money(totals['tax'] or ZERO),
        total_incl_tax=money(totals['total'] or ZERO),
    )
    invoice.refresh_from_db()
    return invoice


@transaction.atomic
def create_invoice(workspace, user, values, lines):
    """Create an editable payable and its explicit reconciliation lines."""
    invoice = SupplierInvoice.objects.create(
        workspace=workspace, created_by=user, **values,
    )
    for line in lines:
        SupplierInvoiceLine.objects.create(invoice=invoice, **line)
    return _refresh_invoice_totals(invoice)


@transaction.atomic
def replace_invoice_draft(invoice, values, lines):
    """Replace all lines and editable header values on a locked draft invoice."""
    invoice = SupplierInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SupplierInvoice.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft supplier invoice can be edited.'})
    invoice.lines.all().delete()
    for field, value in values.items():
        setattr(invoice, field, value)
    invoice.save()
    for line in lines:
        SupplierInvoiceLine.objects.create(invoice=invoice, **line)
    return _refresh_invoice_totals(invoice)


@transaction.atomic
def confirm_invoice(invoice, user):
    """Freeze a payable with supplier identity and calculated money snapshots."""
    del user
    invoice = SupplierInvoice.objects.select_for_update().select_related('supplier').get(pk=invoice.pk)
    if invoice.status != SupplierInvoice.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft supplier invoice can be confirmed.'})
    if not invoice.lines.exists():
        raise ValidationError({'lines': 'Add at least one line before confirming.'})
    _refresh_invoice_totals(invoice)
    SupplierInvoice.objects.filter(pk=invoice.pk).update(
        status=SupplierInvoice.Status.CONFIRMED,
        supplier_name_snapshot=invoice.supplier.name,
        supplier_address_snapshot=invoice.supplier.address,
        supplier_gst_number_snapshot=invoice.supplier.gst_number,
        confirmed_at=timezone.now(),
    )
    invoice.refresh_from_db()
    return invoice


@transaction.atomic
def issue_invoice_correction(invoice, values, user):
    """Append a bounded credit or a debit to a confirmed invoice."""
    invoice = SupplierInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SupplierInvoice.Status.CONFIRMED:
        raise ValidationError({'invoice': 'Only a confirmed invoice can be corrected.'})
    if values['kind'] == SupplierInvoiceCorrection.Kind.CREDIT:
        prior_credits = invoice.corrections.filter(
            kind=SupplierInvoiceCorrection.Kind.CREDIT,
        ).aggregate(total=Sum('total_incl_tax'))['total'] or ZERO
        prior_debits = invoice.corrections.filter(
            kind=SupplierInvoiceCorrection.Kind.DEBIT,
        ).aggregate(total=Sum('total_incl_tax'))['total'] or ZERO
        if prior_credits + values['total_incl_tax'] > invoice.total_incl_tax + prior_debits:
            raise ValidationError({'total_incl_tax': 'Credit exceeds the remaining invoice value.'})
    return SupplierInvoiceCorrection.objects.create(
        workspace=invoice.workspace, invoice=invoice, created_by=user, **values,
    )


@transaction.atomic
def record_supplier_payment(workspace, user, values, allocations):
    """Record one payment and explicit invoice allocations atomically."""
    amount = money(values['amount'])
    allocated = money(sum((entry['amount'] for entry in allocations), ZERO))
    if allocated > amount:
        raise ValidationError({'allocations': 'Allocated amount exceeds the payment.'})
    payment = SupplierPayment.objects.create(
        workspace=workspace, created_by=user, **values,
    )
    for allocation in allocations:
        SupplierPaymentAllocation.objects.create(payment=payment, **allocation)
    return payment


@transaction.atomic
def reverse_supplier_payment(payment, user, reason):
    """Append an equal compensating payment and leave the original readable."""
    payment = SupplierPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.reversal_of_id:
        raise ValidationError({'payment': 'A reversal cannot itself be reversed.'})
    if hasattr(payment, 'reversal'):
        raise ValidationError({'payment': 'This payment is already reversed.'})
    return SupplierPayment.objects.create(
        workspace=payment.workspace,
        supplier=payment.supplier,
        paid_on=timezone.localdate(),
        amount=payment.amount,
        currency_code=payment.currency_code,
        method=payment.method,
        external_reference=payment.external_reference,
        notes=reason,
        reversal_of=payment,
        created_by=user,
    )


@transaction.atomic
def confirm_expense(expense, user):
    """Freeze a reviewed non-stock cost independently of inventory."""
    del user
    expense = BusinessExpense.objects.select_for_update().get(pk=expense.pk)
    if expense.status != BusinessExpense.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft expense can be confirmed.'})
    BusinessExpense.objects.filter(pk=expense.pk).update(
        status=BusinessExpense.Status.CONFIRMED, confirmed_at=timezone.now(),
    )
    expense.refresh_from_db()
    return expense
