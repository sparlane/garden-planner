"""Derived purchasing, due-payment, committed-spend, and expense reporting."""

# pylint: disable=too-many-locals

from django.db.models import Sum

from inventory.models import StockReceiptLine

from .models import BusinessExpense, PurchaseOrder, PurchaseRequisition, SupplierInvoice, SupplierPayment, ZERO
from .services import invoice_state, money, order_line_state


def purchasing_summary(workspace, as_of):
    """Reconcile commitments, liabilities, cash, expenses, and quality findings."""
    requisitions = PurchaseRequisition.objects.filter(workspace=workspace)
    orders = PurchaseOrder.objects.filter(workspace=workspace).prefetch_related(
        'lines__receipt_matches__receipt_line__receipt',
    )
    invoices = SupplierInvoice.objects.filter(
        workspace=workspace, status=SupplierInvoice.Status.CONFIRMED,
    ).select_related('supplier').prefetch_related(
        'lines__receipt_line', 'corrections', 'payment_allocations__payment',
    )
    expenses = BusinessExpense.objects.filter(
        workspace=workspace, status=BusinessExpense.Status.CONFIRMED,
    )
    committed = ZERO
    overdue = []
    invoice_rows = []
    warnings = []
    for order in orders.filter(status=PurchaseOrder.Status.CONFIRMED):
        for line in order.lines.all():
            state = order_line_state(line)
            if line.base_quantity:
                committed += money(
                    line.total_incl_tax * state['outstanding'] / line.base_quantity,
                )
            if order.expected_on and order.expected_on < as_of and state['outstanding'] > 0:
                warnings.append({
                    'code': 'order_overdue',
                    'source_type': 'purchase_order',
                    'source_id': order.pk,
                    'message': f'{order.order_number} has overdue outstanding stock.',
                })
    for invoice in invoices:
        state = invoice_state(invoice)
        row = {
            'invoice': invoice.pk,
            'reference': invoice.external_reference,
            'supplier': invoice.supplier.name,
            'invoice_date': invoice.invoice_date,
            'due_date': invoice.due_date,
            **state,
        }
        invoice_rows.append(row)
        if invoice.due_date and invoice.due_date < as_of and state['balance_due'] > 0:
            overdue.append(row)
        for message in state['warnings']:
            warnings.append({
                'code': 'invoice_unmatched' if 'unmatched' in message.lower() else 'invoice_quality',
                'source_type': 'supplier_invoice',
                'source_id': invoice.pk,
                'message': message,
            })
        for line in invoice.lines.all():
            if line.receipt_line_id and line.total_incl_tax != line.receipt_line.supplier_cost_incl_tax:
                warnings.append({
                    'code': 'invoice_receipt_price_difference',
                    'source_type': 'supplier_invoice',
                    'source_id': invoice.pk,
                    'message': 'Invoice line differs from the posted receipt cost; lot valuation was not changed.',
                })
    unmatched_receipts = StockReceiptLine.objects.filter(
        receipt__workspace=workspace,
        receipt__status='posted',
        supplier_invoice_lines=None,
    ).values_list('pk', flat=True)
    for line_id in unmatched_receipts:
        warnings.append({
            'code': 'receipt_not_invoiced',
            'source_type': 'stock_receipt_line',
            'source_id': line_id,
            'message': 'Posted receipt line is not matched to a supplier invoice.',
        })
    live_payments = SupplierPayment.objects.filter(
        workspace=workspace, reversal_of=None, reversal__isnull=True,
    )
    expense_totals = expenses.aggregate(
        subtotal=Sum('subtotal_ex_tax'), tax=Sum('tax_total'), total=Sum('total_incl_tax'),
    )
    return {
        'as_of': as_of,
        'requisitions': {
            status: requisitions.filter(status=status).count()
            for status in PurchaseRequisition.Status.values
        },
        'committed_spend': money(committed),
        'invoices': invoice_rows,
        'overdue_invoices': overdue,
        'cash_paid': money(live_payments.aggregate(total=Sum('amount'))['total'] or ZERO),
        'expenses': {
            'subtotal_ex_tax': money(expense_totals['subtotal'] or ZERO),
            'tax_total': money(expense_totals['tax'] or ZERO),
            'total_incl_tax': money(expense_totals['total'] or ZERO),
        },
        'warnings': warnings,
    }
