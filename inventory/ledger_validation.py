"""Focused validation helpers for append-only ledger records."""


def movement_validation_errors(movement):
    """Return field errors for a stock movement's domain-specific shape."""
    movement_type = movement.MovementType
    inbound = {
        movement_type.OPENING,
        movement_type.RECEIPT,
        movement_type.ADJUSTMENT_GAIN,
        movement_type.CUSTOMER_RETURN,
    }
    outbound = {
        movement_type.CONSUMPTION,
        movement_type.ADJUSTMENT_LOSS,
        movement_type.WASTE,
        movement_type.SALE,
    }
    errors = {}
    if movement.movement_type in inbound:
        if movement.source_id or not movement.destination_id:
            errors['destination'] = 'This movement requires only a destination.'
    if movement.movement_type in outbound:
        if not movement.source_id or movement.destination_id:
            errors['source'] = 'This movement requires only a source.'
    if movement.movement_type == movement_type.TRANSFER:
        if not movement.source_id or not movement.destination_id:
            errors['destination'] = 'Transfers require a source and destination.'
        elif movement.source_id == movement.destination_id:
            errors['destination'] = 'Transfer locations must be different.'
    _add_reversal_errors(movement, errors)
    _add_document_link_errors(movement, errors)
    return errors


def _add_reversal_errors(movement, errors):
    """Require a reversal to exactly invert one unrepeated original identity."""
    if movement.movement_type == movement.MovementType.REVERSAL:
        if not movement.reversal_of_id:
            errors['reversal_of'] = 'Reversals require an original movement.'
        elif not _reversal_identity_matches(movement):
            errors['reversal_of'] = 'Reversal locations must invert the original movement.'
    elif movement.reversal_of_id:
        errors['reversal_of'] = 'Only reversal movements can link an original.'


def _reversal_identity_matches(movement):
    """Return whether a reversal restores the exact original quantity and places."""
    original = movement.reversal_of
    locations_match = movement.source_id == original.destination_id and movement.destination_id == original.source_id
    identity_matches = movement.lot_id == original.lot_id and movement.quantity == original.quantity
    return locations_match and identity_matches


def _add_document_link_errors(movement, errors):
    """Keep receipt and stocktake links consistent with movement semantics."""
    if movement.movement_type == movement.MovementType.RECEIPT:
        if not movement.receipt_line_id:
            errors['receipt_line'] = 'Receipt movements require a receipt line.'
        elif movement.lot_id and movement.lot.receipt_line_id != movement.receipt_line_id:
            errors['receipt_line'] = 'The receipt line does not belong to this lot.'
    elif movement.receipt_line_id:
        errors['receipt_line'] = 'Only receipt movements can link a receipt line.'

    stocktake_types = {
        movement.MovementType.ADJUSTMENT_GAIN,
        movement.MovementType.ADJUSTMENT_LOSS,
    }
    if movement.stocktake_line_id and movement.movement_type not in stocktake_types:
        errors['stocktake_line'] = 'Stocktake lines require an adjustment movement.'
