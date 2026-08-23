"""The register of every taxable supply document and correction issued.

Task 118 change 6 asks for printable and exportable records with stable
identifiers and audit history. The printable half lives in `billing.printing`,
which shapes one document for a page; this is the other half — every document
and every correction as one row, in the same envelope every other report uses,
so it exports to CSV through the same renderer and is filtered the same way.

Corrections are rows here rather than a column on the document they correct.
A credit note is a document in its own right with its own number, its own date
and its own place in a GST return, and folding it into a summary column on the
invoice would make it unexportable as the record it is.
"""

# pylint: disable=duplicate-code

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from billing.documents import document_information, document_state
from billing.models import SupplyCorrection, SupplyDocument
from billing.thresholds import describe, missing_information
from sales.models import Refund

from .common import Report, decimal_string


MONEY_PLACES = 4
ZERO = Decimal('0.0000')

SUPPLY_KIND = 'supply'

COLUMNS = (
    'document_number', 'document_kind', 'document_date', 'order', 'order_number',
    'corrects', 'taxable_supply', 'tier',
    'seller_legal_name', 'seller_trading_name', 'seller_gst_number',
    'customer', 'buyer_name', 'buyer_identification',
    'currency_code', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
    'credited_total', 'net_total_incl_tax',
    'previously_invoiced', 'paid_to_date', 'balance_due', 'overpaid_at_issue',
    'status', 'reason_code', 'reason', 'sales_return', 'refund',
    'line_count', 'missing_information', 'issued_by', 'issued_at',
)

RECONCILIATION = {
    'net_equation': 'net total = total including tax - credited total',
    'correction_equation': (
        'a correction row moves value on the document named in its corrects '
        'column, and never rewrites it'
    ),
    'identifier_note': (
        'Every row carries a document number that is unique within the '
        'workspace and is never reissued, including for a document credited '
        'away in full.'
    ),
    'audit_note': (
        'issued_by and issued_at record who issued each document and when. '
        'Both document kinds are immutable, so the row exported today is the '
        'row that was handed over.'
    ),
    'balance_note': (
        'previously invoiced, paid to date and balance due are what was true '
        'on the document date, snapshotted at issue. They do not move when '
        'later money arrives, which is why they can disagree with the order.'
    ),
}


def supply_document_report(workspace, filters):
    """Return one row per issued document and per correction against one."""
    start, end = _date_bounds(filters)
    documents = _documents(workspace, filters, start, end)
    corrections = _corrections(workspace, filters, start, end)
    rows = [_document_row(document) for document in documents]
    rows.extend(_correction_row(correction) for correction in corrections)
    rows.sort(key=lambda row: (row['document_date'], row['document_number']))
    return Report(
        name='supply-documents',
        filters=dict(filters),
        columns=COLUMNS,
        rows=rows,
        totals=_totals(rows),
        reconciliation=dict(RECONCILIATION),
        data_quality=_data_quality(workspace, documents, start, end),
    )


def _documents(workspace, filters, start, end):
    """Return the documents in range, with everything a row needs prefetched."""
    if filters.get('kind') not in (None, SUPPLY_KIND):
        return []
    queryset = SupplyDocument.objects.filter(
        workspace=workspace, issued_on__gte=start, issued_on__lte=end,
    ).select_related('order').prefetch_related('lines', 'corrections__lines')
    return list(_narrowed(queryset, filters))


def _corrections(workspace, filters, start, end):
    """Return the corrections in range, keyed by their own date, not the supply's."""
    kind = filters.get('kind')
    if kind == SUPPLY_KIND:
        return []
    queryset = SupplyCorrection.objects.filter(
        workspace=workspace, corrected_on__gte=start, corrected_on__lte=end,
    ).select_related('document__order')
    if kind:
        queryset = queryset.filter(correction_type=kind)
    if filters.get('order'):
        queryset = queryset.filter(document__order_id=filters['order'])
    if filters.get('customer'):
        queryset = queryset.filter(customer_id=filters['customer'])
    return list(queryset)


def _narrowed(queryset, filters):
    """Apply the filters a document and a correction share."""
    if filters.get('order'):
        queryset = queryset.filter(order_id=filters['order'])
    if filters.get('customer'):
        queryset = queryset.filter(customer_id=filters['customer'])
    return queryset


def _blank_row():
    """Return a row with every column present, so the CSV never shifts."""
    return {column: None for column in COLUMNS}


def _document_row(document):
    """Render one issued supply document."""
    state = document_state(document)
    missing = missing_information(document_information(document))
    return {
        **_blank_row(),
        'document_number': document.document_number,
        'document_kind': SUPPLY_KIND,
        'document_date': document.issued_on.isoformat(),
        'order': document.order_id,
        'order_number': document.order.order_number,
        'taxable_supply': document.taxable_supply,
        'tier': document.tier,
        'seller_legal_name': document.seller_legal_name,
        'seller_trading_name': document.seller_trading_name,
        'seller_gst_number': document.seller_gst_number,
        'customer': document.customer_id,
        'buyer_name': document.buyer_name,
        'buyer_identification': document.buyer_identification,
        'currency_code': document.currency_code,
        'subtotal_ex_tax': decimal_string(document.subtotal_ex_tax, MONEY_PLACES),
        'tax_total': decimal_string(document.tax_total, MONEY_PLACES),
        'total_incl_tax': decimal_string(document.total_incl_tax, MONEY_PLACES),
        'credited_total': decimal_string(state['credited_total'], MONEY_PLACES),
        'net_total_incl_tax': decimal_string(state['net_total_incl_tax'], MONEY_PLACES),
        'previously_invoiced': decimal_string(document.previously_invoiced, MONEY_PLACES),
        'paid_to_date': decimal_string(document.paid_to_date, MONEY_PLACES),
        'balance_due': decimal_string(document.balance_due, MONEY_PLACES),
        'overpaid_at_issue': decimal_string(document.overpaid_at_issue, MONEY_PLACES),
        'status': state['status'],
        'line_count': document.lines.count(),
        'missing_information': describe(missing) if missing else '',
        'issued_by': document.created_by_id,
        'issued_at': document.created.isoformat(),
    }


def _correction_row(correction):
    """Render one credit or debit note as the document it is."""
    return {
        **_blank_row(),
        'document_number': correction.document_number,
        'document_kind': correction.correction_type,
        'document_date': correction.corrected_on.isoformat(),
        'order': correction.document.order_id,
        'order_number': correction.document.order.order_number,
        'corrects': correction.document.document_number,
        'taxable_supply': correction.document.taxable_supply,
        'seller_legal_name': correction.seller_legal_name,
        'seller_trading_name': correction.seller_trading_name,
        'seller_gst_number': correction.seller_gst_number,
        'customer': correction.customer_id,
        'buyer_name': correction.buyer_name,
        'buyer_identification': correction.buyer_identification,
        'currency_code': correction.currency_code,
        'subtotal_ex_tax': decimal_string(correction.subtotal_ex_tax, MONEY_PLACES),
        'tax_total': decimal_string(correction.tax_total, MONEY_PLACES),
        'total_incl_tax': decimal_string(correction.total_incl_tax, MONEY_PLACES),
        'status': 'issued',
        'reason_code': correction.reason_code,
        'reason': correction.reason,
        'sales_return': correction.sales_return_id,
        'refund': correction.refund_id,
        'line_count': correction.lines.count(),
        'missing_information': '',
        'issued_by': correction.created_by_id,
        'issued_at': correction.created.isoformat(),
    }


def _totals(rows):
    """Total each currency separately, never across them.

    There is no exchange rate in this application — task 121 owns that — so the
    same rule the GST report follows applies here: two currencies are two
    answers, and one consolidated figure would be an invented one.
    """
    summed = defaultdict(lambda: defaultdict(Decimal))
    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        currency = row['currency_code']
        counts[currency][row['document_kind']] += 1
        for field in ('subtotal_ex_tax', 'tax_total', 'total_incl_tax'):
            summed[currency][field] += Decimal(row[field])
    return {
        'documents': len(rows),
        'currencies': sorted(summed),
        'by_currency': {
            currency: {
                **{
                    field: decimal_string(summed[currency][field], MONEY_PLACES)
                    for field in ('subtotal_ex_tax', 'tax_total', 'total_incl_tax')
                },
                'counts': dict(counts[currency]),
            }
            for currency in sorted(summed)
        },
    }


def _data_quality(workspace, documents, start, end):
    """Report the paperwork that is missing or defective, with what it is."""
    findings = []
    defective = [row for row in documents if missing_information(document_information(row))]
    if defective:
        findings.append({
            'code': 'document_information_incomplete',
            'count': len(defective),
            'message': (
                'Some documents no longer state everything their value band '
                'requires. Issuing refuses an incomplete document, so this '
                'means one was written by something other than the issuing '
                'service.'
            ),
            'drill_down': '/reports/supply-documents/',
        })
    uncorrected = _refunds_without_a_correction(workspace, start, end)
    if uncorrected:
        findings.append({
            'code': 'refund_without_a_correction',
            'count': uncorrected,
            'message': (
                'Money was refunded without a credit note being issued for it. '
                'The refund still adjusts a GST return, but the customer has '
                'no supply correction information for the change.'
            ),
            'drill_down': '/reports/supply-documents/',
        })
    return findings


def _refunds_without_a_correction(workspace, start, end):
    """Count effective refunds in range that no correction document evidences."""
    credited = set(
        SupplyCorrection.objects.filter(workspace=workspace, refund__isnull=False)
        .values_list('refund_id', flat=True)
    )
    refunds = Refund.objects.filter(
        workspace=workspace,
        refunded_at__date__gte=start,
        refunded_at__date__lte=end,
        reversal_of__isnull=True,
        reversal__isnull=True,
    ).values_list('pk', flat=True)
    return sum(1 for refund in refunds if refund not in credited)


def _date_bounds(filters):
    """Return the inclusive range to report, defaulting to the current year."""
    start = filters.get('date_from')
    end = filters.get('date_to')
    today = _today()
    return (
        _as_date(start) if start else date(today.year, 1, 1),
        _as_date(end) if end else date(today.year, 12, 31),
    )


def _as_date(value):
    """Accept a filter value as either an ISO string or an already-parsed date."""
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(value).date()


def _today():
    """Return today, isolated so a test can control the default range."""
    from django.utils import timezone  # pylint: disable=import-outside-toplevel
    return timezone.localdate()
