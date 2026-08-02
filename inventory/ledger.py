"""Transactional services for posting and querying the inventory ledger."""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import NamedTuple

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    COST_DECIMAL_PLACES,
    MONEY_DECIMAL_PLACES,
    QUANTITY_DECIMAL_PLACES,
    InventoryItem,
    InventoryUnit,
    InventoryUnitReconciliation,
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
    enforce_source_balance: bool = True


class UnitMovementRequest(NamedTuple):
    """Caller intent for one exact serialized-unit movement."""

    unit: InventoryUnit
    movement_type: str
    destination: object = None
    occurred_at: object = None
    reason: str = ''
    reference: str = ''


class UnitReconciliationRequest(NamedTuple):
    """Caller intent for one legacy unit opening reconciliation."""

    unit: InventoryUnit
    acquisition_cost: Decimal
    destination: object
    occurred_at: object = None
    reason: str = ''


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
    unit: InventoryUnit = None
    source: object = None
    destination: object = None
    occurred_at: object = None
    reason: str = ''
    reference: str = ''
    reversal_of: object = None
    receipt_line: object = None
    stocktake_line: object = None
    enforce_source_balance: bool = True


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


def lock_units(workspace, unit_ids):
    """Lock exact workspace units in deterministic primary-key order."""
    requested = sorted(set(unit_ids))
    units = list(
        InventoryUnit.objects.select_for_update(of=('self',))
        .select_related('item', 'source_lot', 'current_location')
        .filter(workspace=workspace, pk__in=requested)
        .order_by('pk')
    )
    if len(units) != len(requested):
        raise ValidationError({'unit': 'One or more serialized units are unavailable.'})
    return {unit.pk: unit for unit in units}


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
    if entry.source and entry.enforce_source_balance:
        _validate_source_balance(entry.lot, entry.source, quantity)
    movement = StockMovement.objects.create(
        workspace=entry.workspace,
        created_by=entry.user,
        lot=entry.lot,
        unit=entry.unit,
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
    if entry.unit:
        _sync_unit_after_movement(entry.unit, movement)
    return movement


def _sync_unit_after_movement(unit, movement):
    """Maintain the lockable current location and active-state projection."""
    location_id = movement.destination_id
    active = True
    if movement.source_id and not movement.destination_id:
        location_id = None
        if movement.movement_type in {
            StockMovement.MovementType.SALE,
            StockMovement.MovementType.WASTE,
        }:
            active = False
        elif movement.movement_type == StockMovement.MovementType.REVERSAL:
            active = movement.reversal_of.movement_type not in {
                StockMovement.MovementType.RECEIPT,
                StockMovement.MovementType.OPENING,
            }
        else:
            active = unit.active
    InventoryUnit.objects.filter(pk=unit.pk).update(
        current_location_id=location_id,
        active=active,
        updated=timezone.now(),
    )
    unit.current_location_id = location_id
    unit.active = active


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
    if locked_lot.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
        raise ValidationError({
            'lot': 'Use a serialized-unit action for serialized stock.',
        })
    if not locked_lot.item.active:
        raise ValidationError({'lot': 'The lot item is inactive.'})
    if request.source:
        _validate_location(request.source, workspace, 'source')
    if request.destination:
        _validate_location(request.destination, workspace, 'destination')
    if request.movement_type == StockMovement.MovementType.TRANSFER:
        packet_type = request.source.LocationType.SEED_PACKET
        location_types = (
            request.source.location_type,
            request.destination.location_type,
        )
        if packet_type in location_types:
            raise ValidationError({
                'source': 'Seed packet containers cannot be transferred or split.',
            })
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
        enforce_source_balance=request.enforce_source_balance,
    ))


@transaction.atomic
def correct_stock_movement(original, user, replacement, reason):
    """Reverse and replace one standalone movement under ordered lot locks."""
    if replacement.movement_type != StockMovement.MovementType.CONSUMPTION:
        raise ValidationError({'movement_type': 'Corrections require consumption.'})
    workspace = original.workspace
    locked_lots = lock_lots(
        workspace,
        [original.lot_id, replacement.lot.pk],
    )
    original = StockMovement.objects.select_for_update(of=('self',)).select_related(
        'workspace',
        'source',
        'destination',
    ).get(pk=original.pk)
    original.lot = locked_lots[original.lot_id]
    replacement_lot = locked_lots[replacement.lot.pk]
    _validate_reversible(original, reason, False, False)
    _validate_location(replacement.source, workspace, 'source')
    if replacement_lot.item_id != replacement.lot.item_id:
        raise ValidationError({'lot': 'The replacement lot identity changed.'})
    occurred_at = timezone.now()
    reversal = _create_reversal(original, user, reason, occurred_at)
    replacement_movement = _create_movement(MovementEntry(
        workspace=workspace,
        user=user,
        lot=replacement_lot,
        movement_type=StockMovement.MovementType.CONSUMPTION,
        quantity=replacement.quantity,
        source=replacement.source,
        occurred_at=occurred_at,
        reason=reason,
        reference=replacement.reference,
        enforce_source_balance=replacement.enforce_source_balance,
    ))
    return reversal, replacement_movement


@transaction.atomic
def post_opening_balance(workspace, user, request):
    """Create a costed opening lot and its initial inbound movement."""
    item = InventoryItem.objects.select_for_update().get(
        workspace=workspace,
        pk=request.item.pk,
    )
    if not item.active:
        raise ValidationError({'item': 'The item is inactive.'})
    if item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
        raise ValidationError({
            'item': 'Receive serialized items through their unit workflow.',
        })
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


def _serialized_unit_costs(total, quantity):
    """Split a receipt total exactly at stored currency precision."""
    count = int(quantity)
    share = (total / count).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)
    remainder_steps = int((total - (share * count)) / MONEY_QUANTUM)
    return [
        share + (MONEY_QUANTUM if index < remainder_steps else Decimal('0'))
        for index in range(count)
    ]


def _validate_serialized_receipt_line(line):
    """Require a whole, exact count for individually created units."""
    if line.quantity_certainty != QuantityCertainty.EXACT:
        raise ValidationError({
            'lines': f'Serialized line {line.pk} requires an exact quantity.',
        })
    if line.base_quantity != line.base_quantity.to_integral_value():
        raise ValidationError({
            'lines': f'Serialized line {line.pk} requires a whole quantity.',
        })


@transaction.atomic
def post_receipt(receipt, user):  # pylint: disable=too-many-branches
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
        if line.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
            _validate_serialized_receipt_line(line)

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
        if line.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
            for acquisition_cost in _serialized_unit_costs(
                acquisition_total,
                line.base_quantity,
            ):
                unit = InventoryUnit.objects.create(
                    workspace=receipt.workspace,
                    item=line.item,
                    source_lot=lot,
                    acquisition_cost=acquisition_cost,
                    currency_code=receipt.currency_code,
                )
                _create_movement(MovementEntry(
                    workspace=receipt.workspace,
                    user=user,
                    lot=lot,
                    unit=unit,
                    movement_type=StockMovement.MovementType.RECEIPT,
                    quantity=Decimal('1'),
                    destination=line.destination,
                    occurred_at=posted_at,
                    reference=receipt.supplier_reference,
                    receipt_line=line,
                ))
        elif line.quantity_certainty != QuantityCertainty.UNKNOWN:
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
    removes_unit = original.unit_id and original.destination_id and not original.source_id
    if removes_unit and unit_is_in_use(original.unit):
        raise ValidationError({
            'unit': 'Move or dispose of active plants before removing this tray.',
        })


def _create_reversal(original, user, reason, occurred_at):
    """Append an inverse movement after validation and lot locking."""
    return _create_movement(MovementEntry(
        workspace=original.workspace,
        user=user,
        lot=original.lot,
        unit=original.unit,
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
    unit = None
    if original.unit_id:
        unit = lock_units(original.workspace, [original.unit_id])[original.unit_id]
    original = StockMovement.objects.select_for_update(of=('self',)).select_related(
        'workspace',
        'source',
        'destination',
    ).get(pk=original.pk)
    original.lot = lot
    original.unit = unit
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
    locked_units = lock_units(
        workspace,
        [row.unit_id for row in movements if row.unit_id],
    )
    occurred_at = timezone.now()
    for original in movements:
        original.lot = locked_lots[original.lot_id]
        if original.unit_id:
            original.unit = locked_units[original.unit_id]
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


def unit_is_in_use(unit):
    """Return whether cultivation still occupies the linked physical tray."""
    try:
        tray = unit.seed_tray
    except ObjectDoesNotExist:
        return False
    from plantings.models import SeedTrayPlanting, SpecificPlantLocation  # pylint: disable=import-outside-toplevel

    active_sowing = SeedTrayPlanting.objects.filter(
        seed_tray=tray,
        removed=False,
    ).exists()
    active_plant = SpecificPlantLocation.objects.filter(
        seed_tray_cell__tray=tray,
        ended__isnull=True,
    ).exists()
    return active_sowing or active_plant


def unit_physical_state(unit):
    """Derive a unit state from non-reversed movements and current location."""
    if unit.current_location_id:
        if unit.current_location.location_type == unit.current_location.LocationType.QUARANTINE:
            return 'quarantined'
        latest = _latest_effective_unit_movement(unit)
        if latest and latest.movement_type in {
            StockMovement.MovementType.ADJUSTMENT_GAIN,
            StockMovement.MovementType.CUSTOMER_RETURN,
        }:
            return 'returned'
        return 'available'
    latest = _latest_effective_unit_movement(unit)
    if latest:
        states = {
            StockMovement.MovementType.ADJUSTMENT_LOSS: 'lost',
            StockMovement.MovementType.WASTE: 'retired',
            StockMovement.MovementType.SALE: 'dispatched',
        }
        if latest.movement_type in states:
            return states[latest.movement_type]
    return 'retired'


def _latest_effective_unit_movement(unit):
    """Ignore reversal pairs when locating the unit's latest real event."""
    return (
        StockMovement.objects.filter(unit=unit, reversal_of__isnull=True)
        .filter(reversal__isnull=True)
        .order_by('-occurred_at', '-pk')
        .first()
    )


@transaction.atomic
def post_unit_movement(workspace, user, request):
    """Post one physical action against an exact locked serialized unit."""
    lot = lock_lots(workspace, [request.unit.source_lot_id])[
        request.unit.source_lot_id
    ]
    unit = lock_units(workspace, [request.unit.pk])[request.unit.pk]
    if not unit.item.active:
        raise ValidationError({'unit': 'The serialized item is inactive.'})
    if request.destination:
        _validate_location(request.destination, workspace, 'destination')
    state = unit_physical_state(unit)
    source = unit.current_location
    destination = request.destination
    allowed = {
        StockMovement.MovementType.TRANSFER,
        StockMovement.MovementType.ADJUSTMENT_LOSS,
        StockMovement.MovementType.WASTE,
        StockMovement.MovementType.ADJUSTMENT_GAIN,
    }
    if request.movement_type not in allowed:
        raise ValidationError({'movement_type': 'Use a supported unit action.'})
    if request.movement_type == StockMovement.MovementType.TRANSFER:
        if not source:
            raise ValidationError({'unit': 'The unit is not currently on hand.'})
        if not destination or source.pk == destination.pk:
            raise ValidationError({'destination': 'Choose a different destination.'})
    elif request.movement_type in {
        StockMovement.MovementType.ADJUSTMENT_LOSS,
        StockMovement.MovementType.WASTE,
    }:
        if not source:
            raise ValidationError({'unit': 'The unit is not currently on hand.'})
        if unit_is_in_use(unit):
            raise ValidationError({
                'unit': 'Move or dispose of active plants before removing this tray.',
            })
        destination = None
    else:
        if state not in {'lost', 'retired'}:
            raise ValidationError({'unit': 'Only a lost or retired unit can be returned.'})
        if not destination:
            raise ValidationError({'destination': 'A return destination is required.'})
        source = None
    movement = _create_movement(MovementEntry(
        workspace=workspace,
        user=user,
        lot=lot,
        unit=unit,
        movement_type=request.movement_type,
        quantity=Decimal('1'),
        source=source,
        destination=destination,
        occurred_at=request.occurred_at,
        reason=request.reason,
        reference=request.reference,
    ))
    return movement


@transaction.atomic
def reconcile_unit_opening(workspace, user, request):
    """Supply audited cost and location for one migrated tray unit."""
    lot = lock_lots(workspace, [request.unit.source_lot_id])[
        request.unit.source_lot_id
    ]
    unit = lock_units(workspace, [request.unit.pk])[request.unit.pk]
    if hasattr(unit, 'opening_reconciliation'):
        raise ValidationError({'unit': 'This unit has already been reconciled.'})
    if unit.source_lot.origin != StockLot.Origin.OPENING:
        raise ValidationError({'unit': 'Only opening units can be reconciled.'})
    if not unit.current_location_id or unit.current_location.code != 'SYSTEM-TRAY-UNKNOWN':
        raise ValidationError({'unit': 'The unit is no longer at its unknown location.'})
    _validate_location(request.destination, workspace, 'destination')
    if request.destination.pk == unit.current_location_id:
        raise ValidationError({'destination': 'Choose an audited physical location.'})
    cost = Decimal(request.acquisition_cost).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if cost < 0:
        raise ValidationError({'acquisition_cost': 'Cost cannot be negative.'})
    movement = _create_movement(MovementEntry(
        workspace=workspace,
        user=user,
        lot=lot,
        unit=unit,
        movement_type=StockMovement.MovementType.TRANSFER,
        quantity=Decimal('1'),
        source=unit.current_location,
        destination=request.destination,
        occurred_at=request.occurred_at,
        reason=request.reason,
        reference='Opening inventory reconciliation',
    ))
    InventoryUnit.objects.filter(pk=unit.pk).update(
        acquisition_cost=cost,
        updated=timezone.now(),
    )
    reconciliation = InventoryUnitReconciliation.objects.create(
        workspace=workspace,
        unit=unit,
        acquisition_cost=cost,
        movement=movement,
        reason=request.reason,
        recorded_by=user,
    )
    unit.acquisition_cost = cost
    return reconciliation


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
        if line.lot.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
            raise ValidationError({
                'lines': 'Count serialized stock through unit actions.',
            })
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
