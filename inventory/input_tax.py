"""Input-tax evidence warnings that never silently alter an operator's claim."""

from tax.periods import registration_in_force

from .models import StockReceiptLine


def receipt_tax_warnings(receipt):
    """Return actionable warnings for explicit claims on one receipt."""
    lines = list(receipt.lines.all())
    claimed = [line for line in lines if line.recoverable_input_tax > 0]
    warnings = []
    if not claimed:
        return warnings

    claim_date = receipt.invoice_date or receipt.received_date
    if registration_in_force(receipt.workspace, claim_date) is None:
        warnings.append(_warning(
            'workspace_not_registered',
            'Input tax is claimed on a date when the workspace has no GST registration.',
        ))
    if not _has_evidence(receipt):
        warnings.append(_warning(
            'purchase_evidence_missing',
            'Input tax is claimed without a source document number, evidence reference, or evidence URL.',
        ))
    if receipt.invoice_date is None:
        warnings.append(_warning(
            'invoice_date_missing',
            'No supplier invoice date is recorded, so the received date remains a proxy.',
        ))

    for line in claimed:
        warnings.extend(_line_warnings(receipt, line))
    return warnings


def _line_warnings(receipt, line):
    """Return evidence warnings specific to one claimed line."""
    warnings = []
    if line.legacy_tax_classification:
        warnings.append(_warning(
            'legacy_unverified_claim',
            'This claim was migrated from the former receipt-wide recoverability flag.',
            line.pk,
        ))
    if line.tax_treatment == StockReceiptLine.TaxTreatment.UNKNOWN:
        warnings.append(_warning(
            'unknown_purchase_tax_treatment',
            'A claimed line still has unknown GST treatment.',
            line.pk,
        ))
    if line.input_tax_source == StockReceiptLine.InputTaxSource.SUPPLIER:
        if receipt.supplier_gst_status != 'registered':
            warnings.append(_warning(
                'supplier_registration_unsupported',
                'Supplier-charged GST is claimed without recorded supplier registration.',
                line.pk,
            ))
        if not receipt.supplier_gst_number:
            warnings.append(_warning(
                'supplier_gst_number_missing',
                'Supplier-charged GST is claimed without the supplier GST number.',
                line.pk,
            ))
    if line.input_tax_source == StockReceiptLine.InputTaxSource.CUSTOMS:
        if receipt.source_document_type != receipt.SourceDocumentType.CUSTOMS_ENTRY:
            warnings.append(_warning(
                'customs_evidence_missing',
                'Customs GST is claimed without a Customs entry or statement as the source record.',
                line.pk,
            ))
    if line.input_tax_source == StockReceiptLine.InputTaxSource.SECOND_HAND:
        if receipt.supplier_gst_status != 'unregistered':
            warnings.append(_warning(
                'second_hand_supplier_status_missing',
                'A second-hand-goods deduction needs the seller recorded as unregistered.',
                line.pk,
            ))
        if not receipt.supplier_name_snapshot or not receipt.supplier_address_snapshot:
            warnings.append(_warning(
                'second_hand_seller_details_missing',
                'A second-hand-goods deduction needs the seller name and address.',
                line.pk,
            ))
    return warnings


def _has_evidence(receipt):
    return any((
        receipt.source_document_number.strip(),
        receipt.evidence_reference.strip(),
        receipt.evidence_url.strip(),
    ))


def _warning(code, message, line_id=None):
    return {'code': code, 'message': message, 'line_id': line_id}
