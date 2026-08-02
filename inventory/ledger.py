"""Transactional services for posting and querying the inventory ledger."""

from decimal import Decimal, ROUND_HALF_UP
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    COST_DECIMAL_PLACES,
    MONEY_DECIMAL_PLACES,
    QUANTITY_DECIMAL_PLACES,
    InventoryItem,
    QuantityCertainty,
    StockLot,
    StockMovement,
    StockReceipt,
    Stocktake,
)
from .units import convert_standard_quantity


QUANTITY_QUANTUM = Decimal(1).scaleb(-QUANTITY_DECIMAL_PLACES)
MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_DECIMAL_PLACES)
COST_QUANTUM = Decimal(1).scaleb(-COST_DECIMAL_PLACES)


class MovementRequest(NamedTuple):
    """Validated caller intent for one standalone stock movement."""

    lot: StockLot
    movement_type: str
    quantity: Decimal
    source: object = None
    destination: object = None
    occurred_at: object = None
    reason: str = ''
    reference: str = ''


class OpeningBalanceRequest(NamedTuple):
    """Caller intent for a costed opening lot and movement."""

    item: InventoryItem
    quantity: Decimal
    destination: object
    acquisition_total: Decimal
    received_on: object
    supplier_lot_reference: str = ''
    expires_on: object = None
    occurred_at: object = None
    reason: str = ''


class MovementEntry(NamedTuple):
    """Complete internal data needed to append one movement row."""

    workspace: object
    user: object
    lot: StockLot
    movement_type: str
    quantity: Decimal
    source: object = None
    destination: object = None
    occurred_at: object = None
    reason: str = ''
    reference: str = ''
    reversal_of: object = None
    receipt_line: object = None
    stocktake_line: object = None


def quantize_quantity(value):
    """Return a canonical base-unit quantity at ledger precision."""
    return Decimal(value).quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def normalize_quantity(
    item,
    quantity,
    unit_code=None,
    unit_conversion=None,
    allow_zero=False,
):
    """Normalize one controlled or item-package display quantity."""
    if bool(unit_code) == bool(unit_conversion):
        raise ValidationError(
            {'unit_code': 'Select exactly one controlled unit or item conversion.'},
        )
    if unit_conversion:
        if unit_conversion.item_id != item.pk:
            raise ValidationError(
                {'unit_conversion': 'The conversion does not belong to this item.'},
            )
        normalized = Decimal(quantity) * unit_conversion.multiplier
    else:
        normalized = convert_standard_quantity(quantity, unit_code, item.base_unit)
    normalized = quantize_quantity(normalized)
    if normalized < 0 or (not allow_zero and normalized == 0):
        raise ValidationError({'quantity': 'Quantity must be greater than zero.'})
    return normalized


def physical_balance(lot, location):
    """Derive one lot/location balance from append-only movement rows."""
    totals = StockMovement.objects.filter(lot=lot).aggregate(
        incoming=Sum('quantity', filter=Q(destination=location)),
        outgoing=Sum('quantity', filter=Q(source=location)),
    )
    return (totals['incoming'] or Decimal('0')) - (
        totals['outgoing'] or Decimal('0')
    )


def lock_lots(workspace, lot_ids):
    """Lock and return exact workspace lots in deterministic primary-key order."""
    requested = sorted(set(lot_ids))
    lots = list(
        StockLot.objects.select_for_update()
        .select_related('item')
        .filter(workspace=workspace, pk__in=requested)
        .order_by('pk')
    )
    if len(lots) != len(requested):
        raise ValidationError({'lot': 'One or more lots are unavailable.'})
    return {lot.pk: lot for lot in lots}


def _validate_location(location, workspace, field_name, require_active=True):
    """Require an operation location in the current workspace."""
    if not location or location.workspace_id != workspace.pk:
        raise ValidationError(
            {field_name: 'The location belongs to a different workspace.'},
        )
    if require_active and not location.active:
        raise ValidationError({field_name: 'The location is inactive.'})


def _validate_source_balance(lot, source, quantity):
    """Reject an outbound effect that exceeds physical stock at its source."""
    available = physical_balance(lot, source)
    if quantity > available:
        raise ValidationError(
            {
                'quantity': (
                    f'Only {available:.9f} {lot.item.base_unit} is available '
                    f'at {source.name}.'
                ),
            },
        )


def _create_movement(entry):
    """Create a validated movement after callers acquire the lot lock."""
    quantity = quantize_quantity(entry.quantity)
    if entry.source:
        _validate_source_balance(entry.lot, entry.source, quantity)
    return StockMovement.objects.create(
        workspace=entry.workspace,
        created_by=entry.user,
        lot=entry.lot,
        movement_type=entry.movement_type,
        quantity=quantity,
        source=entry.source,
        destination=entry.destination,
        occurred_at=entry.occurred_at or timezone.now(),
        reason=entry.reason.strip(),
        reference=entry.reference.strip(),
        reversal_of=entry.reversal_of,
        receipt_line=entry.receipt_line,
        stocktake_line=entry.stocktake_line,
    )


@transaction.atomic
def post_stock_movement(workspace, user, request):
    """Post one typed non-document movement under an exact lot lock."""
    allowed = {
        StockMovement.MovementType.CONSUMPTION,
        StockMovement.MovementType.TRANSFER,
        StockMovement.MovementType.ADJUSTMENT_GAIN,
        StockMovement.MovementType.ADJUSTMENT_LOSS,
        StockMovement.MovementType.WASTE,
        StockMovement.MovementType.SALE,
        StockMovement.MovementType.CUSTOMER_RETURN,
    }
    if request.movement_type not in allowed:
        raise ValidationError({'movement_type': 'Use a supported domain action.'})
    locked_lot = lock_lots(workspace, [request.lot.pk])[request.lot.pk]
    if not locked_lot.item.active:
        raise ValidationError({'lot': 'The lot item is inactive.'})
    if request.source:
        _validate_location(request.source, workspace, 'source')
    if request.destination:
        _validate_location(request.destination, workspace, 'destination')
    if request.movement_type in {
        StockMovement.MovementType.ADJUSTMENT_GAIN,
        StockMovement.MovementType.ADJUSTMENT_LOSS,
        StockMovement.MovementType.WASTE,
    } and not request.reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})
    return _create_movement(MovementEntry(
        workspace=workspace,
        user=user,
        lot=locked_lot,
        movement_type=request.movement_type,
        quantity=request.quantity,
        source=request.source,
        destination=request.destination,
        occurred_at=request.occurred_at,
        reason=request.reason,
        reference=request.reference,
    ))


@transaction.atomic
def post_opening_balance(workspace, user, request):
    """Create a costed opening lot and its initial inbound movement."""
    item = InventoryItem.objects.select_for_update().get(
        workspace=workspace,
        pk=request.item.pk,
    )
    if not item.active:
        raise ValidationError({'item': 'The item is inactive.'})
    _validate_location(request.destination, workspace, 'destination')
    quantity = quantize_quantity(request.quantity)
    if quantity <= 0:
        raise ValidationError({'quantity': 'Quantity must be greater than zero.'})
    total = Decimal(request.acquisition_total).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if total < 0:
        raise ValidationError(
            {'acquisition_total': 'Acquisition total cannot be negative.'},
        )
    unit_cost = (total / quantity).quantize(
        COST_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    lot = StockLot.objects.create(
        workspace=workspace,
        item=item,
        origin=StockLot.Origin.OPENING,
        supplier_lot_reference=request.supplier_lot_reference.strip(),
        received_on=request.received_on,
        expires_on=request.expires_on,
        initial_base_quantity=quantity,
        acquisition_total=total,
        base_unit_cost=unit_cost,
        currency_code=workspace.currency_code,
    )
    movement = _create_movement(MovementEntry(
        workspace=workspace,
        user=user,
        lot=lot,
        movement_type=StockMovement.MovementType.OPENING,
        quantity=quantity,
        destination=request.destination,
        occurred_at=request.occurred_at,
        reason=request.reason,
    ))
    item.mark_stock_history_started(movement.occurred_at)
    return lot, movement


def _receipt_acquisition_cost(receipt, line):
    """Return acquisition total excluding only recoverable tax."""
    line_cost = line.line_cost_ex_tax.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if receipt.tax_recoverable:
        return line_cost
    tax = (line_cost * receipt.tax_rate / Decimal('100')).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return line_cost + tax


@transaction.atomic
def post_receipt(receipt, user):
    """Create all receipt lots and movements or roll the document back."""
    receipt = StockReceipt.objects.select_for_update().select_related(
        'workspace',
    ).get(pk=receipt.pk)
    if receipt.status != StockReceipt.Status.DRAFT:
        raise ValidationError({'status': 'Only draft receipts can be posted.'})
    if receipt.currency_code != receipt.workspace.currency_code:
        raise ValidationError(
            {'currency_code': 'Receipts must use the workspace currency.'},
        )
    lines = list(
        receipt.lines.select_related(
            'item',
            'unit_conversion',
            'destination',
        ).order_by('pk')
    )
    if not lines:
        raise ValidationError({'lines': 'Add at least one receipt line.'})
    item_ids = sorted({line.item_id for line in lines})
    locked_items = {
        item.pk: item
        for item in InventoryItem.objects.select_for_update()
        .filter(workspace=receipt.workspace, pk__in=item_ids)
        .order_by('pk')
    }
    if len(locked_items) != len(item_ids):
        raise ValidationError({'lines': 'One or more items are unavailable.'})
    for line in lines:
        line.full_clean()
        if not locked_items[line.item_id].active:
            raise ValidationError({'lines': f'Item {line.item_id} is inactive.'})
        if not line.destination.active:
            raise ValidationError(
                {'lines': f'Location {line.destination_id} is inactive.'},
            )
        if line.unit_conversion_id and not line.unit_conversion.active:
            raise ValidationError(
                {'lines': f'Conversion {line.unit_conversion_id} is inactive.'},
            )

    posted_at = timezone.now()
    lots = []
    for line in lines:
        acquisition_total = _receipt_acquisition_cost(receipt, line)
        unit_cost = None
        if line.base_quantity is not None:
            unit_cost = (acquisition_total / line.base_quantity).quantize(
                COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        lot = StockLot.objects.create(
            workspace=receipt.workspace,
            item=line.item,
            origin=StockLot.Origin.RECEIPT,
            receipt_line=line,
            supplier_lot_reference=line.supplier_lot_reference,
            received_on=receipt.received_date,
            expires_on=line.expires_on,
            initial_base_quantity=line.base_quantity,
            quantity_certainty=line.quantity_certainty,
            acquisition_total=acquisition_total,
            base_unit_cost=unit_cost,
            currency_code=receipt.currency_code,
        )
        if line.quantity_certainty != QuantityCertainty.UNKNOWN:
            _create_movement(MovementEntry(
                workspace=receipt.workspace,
                user=user,
                lot=lot,
                movement_type=StockMovement.MovementType.RECEIPT,
                quantity=line.base_quantity,
                destination=line.destination,
                occurred_at=posted_at,
                reference=receipt.supplier_reference,
                receipt_line=line,
            ))
        lots.append(lot)
    InventoryItem.objects.filter(
        pk__in=item_ids,
        stock_history_started_at__isnull=True,
    ).update(stock_history_started_at=posted_at)
    StockReceipt.objects.filter(pk=receipt.pk).update(
        status=StockReceipt.Status.POSTED,
        posted_at=posted_at,
        updated=posted_at,
    )
    receipt.refresh_from_db()
    return receipt, lots


def _validate_reversible(original, reason, allow_receipt, allow_stocktake):
    """Validate one original before any reversal row is written."""
    if not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})
    if original.movement_type == StockMovement.MovementType.REVERSAL:
        raise ValidationError({'movement': 'A reversal cannot itself be reversed.'})
    if hasattr(original, 'reversal'):
        raise ValidationError({'movement': 'This movement is already reversed.'})
    if original.receipt_line_id and not allow_receipt:
        raise ValidationError(
            {'movement': 'Reverse receipt movements through their receipt.'},
        )
    if original.stocktake_line_id and not allow_stocktake:
        raise ValidationError(
            {'movement': 'Reverse stocktake movements through their stocktake.'},
        )
    if original.destination_id:
        _validate_source_balance(
            original.lot,
            original.destination,
            original.quantity,
        )


def _create_reversal(original, user, reason, occurred_at):
    """Append an inverse movement after validation and lot locking."""
    return _create_movement(MovementEntry(
        workspace=original.workspace,
        user=user,
        lot=original.lot,
        movement_type=StockMovement.MovementType.REVERSAL,
        quantity=original.quantity,
        source=original.destination,
        destination=original.source,
        occurred_at=occurred_at,
        reason=reason,
        reference=f'Reversal of movement {original.pk}',
        reversal_of=original,
    ))


@transaction.atomic
def reverse_movement(original, user, reason, occurred_at=None):
    """Reverse one standalone movement while retaining the original row."""
    lot = lock_lots(original.workspace, [original.lot_id])[original.lot_id]
    original = StockMovement.objects.select_for_update(of=('self',)).select_related(
        'workspace',
        'source',
        'destination',
    ).get(pk=original.pk)
    original.lot = lot
    _validate_reversible(original, reason, False, False)
    return _create_reversal(
        original,
        user,
        reason,
        occurred_at or timezone.now(),
    )


def _reverse_document_movements(
    workspace,
    movements,
    user,
    reason,
    document_kind,
):
    """Validate and reverse a complete document under deterministic lot locks."""
    locked_lots = lock_lots(workspace, [row.lot_id for row in movements])
    occurred_at = timezone.now()
    for original in movements:
        original.lot = locked_lots[original.lot_id]
        _validate_reversible(
            original,
            reason,
            document_kind == 'receipt',
            document_kind == 'stocktake',
        )
    return [
        _create_reversal(original, user, reason, occurred_at)
        for original in movements
    ]


@transaction.atomic
def reverse_receipt(receipt, user, reason):
    """Reverse every lot created by one posted receipt atomically."""
    receipt = StockReceipt.objects.select_for_update().select_related(
        'workspace',
    ).get(pk=receipt.pk)
    if receipt.status != StockReceipt.Status.POSTED:
        raise ValidationError({'status': 'Only posted receipts can be reversed.'})
    movements = list(
        StockMovement.objects.select_for_update(of=('self',))
        .select_related('lot__item', 'workspace', 'source', 'destination')
        .filter(receipt_line__receipt=receipt)
        .order_by('pk')
    )
    receipt_lot_ids = list(
        StockLot.objects.filter(receipt_line__receipt=receipt)
        .values_list('pk', flat=True)
    )
    if not movements and StockMovement.objects.filter(lot_id__in=receipt_lot_ids).exists():
        raise ValidationError({
            'status': 'A quantity-unknown receipt cannot be reversed after stock use.',
        })
    reversals = _reverse_document_movements(
        receipt.workspace,
        movements,
        user,
        reason,
        'receipt',
    )
    reversed_at = timezone.now()
    StockReceipt.objects.filter(pk=receipt.pk).update(
        status=StockReceipt.Status.REVERSED,
        reversed_at=reversed_at,
        updated=reversed_at,
    )
    receipt.refresh_from_db()
    return receipt, reversals


@transaction.atomic
def post_stocktake(stocktake, user):
    """Snapshot expected counts and post all non-zero variances atomically."""
    stocktake = Stocktake.objects.select_for_update().select_related(
        'workspace',
    ).get(pk=stocktake.pk)
    if stocktake.status != Stocktake.Status.DRAFT:
        raise ValidationError({'status': 'Only draft stocktakes can be posted.'})
    lines = list(
        stocktake.lines.select_related(
            'lot__item',
            'location',
            'unit_conversion',
        ).order_by('pk')
    )
    if not lines:
        raise ValidationError({'lines': 'Add at least one stocktake line.'})
    locked_lots = lock_lots(stocktake.workspace, [line.lot_id for line in lines])
    for line in lines:
        line.lot = locked_lots[line.lot_id]
        line.full_clean()
        if not line.lot.item.active:
            raise ValidationError(
                {'lines': f'Item {line.lot.item_id} is inactive.'},
            )
        _validate_location(line.location, stocktake.workspace, 'location')
        expected = quantize_quantity(physical_balance(line.lot, line.location))
        variance = quantize_quantity(line.counted_base_quantity - expected)
        if variance and not line.reason.strip():
            raise ValidationError(
                {'lines': f'A reason is required for variance line {line.pk}.'},
            )
        line.expected_base_quantity = expected
        line.variance_base_quantity = variance

    posted_at = timezone.now()
    movements = []
    for line in lines:
        type(line).objects.filter(pk=line.pk).update(
            expected_base_quantity=line.expected_base_quantity,
            variance_base_quantity=line.variance_base_quantity,
            updated=posted_at,
        )
        variance = line.variance_base_quantity
        if not variance:
            continue
        movement_type = StockMovement.MovementType.ADJUSTMENT_GAIN
        source = None
        destination = line.location
        if variance < 0:
            movement_type = StockMovement.MovementType.ADJUSTMENT_LOSS
            source = line.location
            destination = None
        movements.append(
            _create_movement(MovementEntry(
                workspace=stocktake.workspace,
                user=user,
                lot=line.lot,
                movement_type=movement_type,
                quantity=abs(variance),
                source=source,
                destination=destination,
                occurred_at=stocktake.counted_at,
                reason=line.reason,
                reference=f'Stocktake {stocktake.pk}',
                stocktake_line=line,
            )),
        )
    Stocktake.objects.filter(pk=stocktake.pk).update(
        status=Stocktake.Status.POSTED,
        posted_at=posted_at,
        updated=posted_at,
    )
    stocktake.refresh_from_db()
    return stocktake, movements


@transaction.atomic
def reverse_stocktake(stocktake, user, reason):
    """Reverse every non-zero stocktake variance atomically."""
    stocktake = Stocktake.objects.select_for_update().select_related(
        'workspace',
    ).get(pk=stocktake.pk)
    if stocktake.status != Stocktake.Status.POSTED:
        raise ValidationError({'status': 'Only posted stocktakes can be reversed.'})
    movements = list(
        StockMovement.objects.select_for_update(of=('self',))
        .select_related('lot__item', 'workspace', 'source', 'destination')
        .filter(stocktake_line__stocktake=stocktake)
        .order_by('pk')
    )
    reversals = _reverse_document_movements(
        stocktake.workspace,
        movements,
        user,
        reason,
        'stocktake',
    )
    reversed_at = timezone.now()
    Stocktake.objects.filter(pk=stocktake.pk).update(
        status=Stocktake.Status.REVERSED,
        reversed_at=reversed_at,
        updated=reversed_at,
    )
    stocktake.refresh_from_db()
    return stocktake, reversals
