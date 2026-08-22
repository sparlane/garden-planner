"""One document shaped for the page it is printed on.

A serializer answers what a record holds; this answers what a customer is
handed. The difference is small but real — the parties become two blocks, the
value band becomes a checklist a reader can see is satisfied, and the running
balance becomes the four lines that explain why the amount due is what it is.

Keeping it out of `rest` means the shaping is testable without a request, and
keeping it out of `documents` means the write path carries no presentation.
"""

from .documents import document_information, document_state, net_credited
from .thresholds import ELEMENT_LABELS, TIER_LABELS, missing_information, required_elements


def _money(value):
    """Render an amount the way every other money field on the API is rendered."""
    return f'{value:f}'


def _party_blocks(document):
    """Return the two identity blocks a printed document leads with."""
    return {
        'seller': {
            'legal_name': document.seller_legal_name,
            'trading_name': document.seller_trading_name,
            'address': document.seller_address,
            'gst_number': document.seller_gst_number,
        },
        'buyer': {
            'name': document.buyer_name,
            'address': document.buyer_address,
            'identifier': document.buyer_identifier,
        },
    }


def _requirement_checklist(document):
    """Say, element by element, whether this document carries what it must.

    Every element is listed rather than only the absent ones. A document that
    quietly showed nothing would look identical whether it was complete or
    whether nobody had checked, and the point of change 5 is that somebody can
    see which.
    """
    information = document_information(document)
    missing = set(missing_information(information))
    return [
        {
            'code': code,
            'label': ELEMENT_LABELS[code],
            'satisfied': code not in missing,
        }
        for code in required_elements(
            document.tier, taxable_supply=document.taxable_supply,
        )
    ]


def _line_rows(document):
    """Return the supply lines, each with the items it covers."""
    rows = []
    for line in document.lines.all():
        coverage = list(line.coverage.all())
        rows.append({
            'pk': line.pk,
            'order_line': line.order_line_id,
            'description': line.description,
            'quantity': line.quantity,
            'unit_price': _money(line.unit_price),
            'tax_rate': _money(line.tax_rate),
            'tax_treatment': line.tax_treatment,
            'subtotal_ex_tax': _money(line.subtotal_ex_tax),
            'tax_total': _money(line.tax_total),
            'total_incl_tax': _money(line.total_incl_tax),
            'credited_total': _money(net_credited(line)),
            'positions': [row.commercial_position for row in coverage],
            'dispatched_positions': [
                row.commercial_position for row in coverage
                if row.fulfillment_line_id is not None
            ],
        })
    return rows


def _correction_rows(document):
    """Return every correction issued against this document, oldest first."""
    return [
        {
            'pk': correction.pk,
            'document_number': correction.document_number,
            'correction_type': correction.correction_type,
            'reason_code': correction.reason_code,
            'reason': correction.reason,
            'corrected_on': correction.corrected_on.isoformat(),
            'sales_return': correction.sales_return_id,
            'refund': correction.refund_id,
            'subtotal_ex_tax': _money(correction.subtotal_ex_tax),
            'tax_total': _money(correction.tax_total),
            'total_incl_tax': _money(correction.total_incl_tax),
        }
        for correction in document.corrections.all()
    ]


def printable_document(document):
    """Return everything one printed taxable supply document shows."""
    state = document_state(document)
    return {
        'pk': document.pk,
        'document_number': document.document_number,
        # The heading is what tells a reader which kind of document this is.
        # A receipt from an unregistered seller is not taxable supply
        # information, and calling it a tax invoice would be a false record.
        'title': 'Tax invoice' if document.taxable_supply else 'Sales receipt',
        'taxable_supply': document.taxable_supply,
        'issued_on': document.issued_on.isoformat(),
        'order': document.order_id,
        'order_number': document.order.order_number,
        'currency_code': document.currency_code,
        'tier': document.tier,
        'tier_label': TIER_LABELS[document.tier],
        'required_information': _requirement_checklist(document),
        'notes': document.notes,
        'lines': _line_rows(document),
        'corrections': _correction_rows(document),
        'totals': {
            'subtotal_ex_tax': _money(document.subtotal_ex_tax),
            'tax_total': _money(document.tax_total),
            'total_incl_tax': _money(document.total_incl_tax),
            'previously_invoiced': _money(document.previously_invoiced),
            'paid_to_date': _money(document.paid_to_date),
            'balance_due': _money(document.balance_due),
            'overpaid_at_issue': _money(document.overpaid_at_issue),
            'credited_total': _money(state['credited_total']),
            'net_total_incl_tax': _money(state['net_total_incl_tax']),
        },
        'status': state['status'],
        'issued_by': document.created_by_id,
        'issued_at': document.created.isoformat(),
        **_party_blocks(document),
    }
