"""Transactional services for posting and querying the inventory ledger."""

# pylint: disable=too-many-lines

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import NamedTuple
from zoneinfo import ZoneInfo

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


class IndividualizationRequest(NamedTuple):
    """Caller intent for numbering part of a mixed lot at one location."""

    lot: StockLot
    location: object
    count: int
    reason: str = ''


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


def quantize_money(value):
    """Return a money amount at the precision the money columns store.

    Deliberately not the same precision as a lot's `base_unit_cost`, which
    carries twelve places. A unit cost is a rate that has to survive being
    multiplied by a large quantity without losing significance; an amount is a
    figure that gets reported and summed, and holding it at anything finer than
    `MONEY_DECIMAL_PLACES` would mean storing digits the column cannot keep.
    """
    return None if value is None else Decimal(value).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def distribute_exactly(total, weights, quantum=MONEY_QUANTUM):
    """Split `total` in proportion to `weights`, losing nothing to rounding.

    Each share is floored to `quantum` and the shortfall is then handed out one
    step at a time, largest fractional part first. The parts therefore always
    sum back to exactly `total`. Rounding each share independently would leave a
    remainder that has to be either dropped, which understates the cost, or
    absorbed into one arbitrary share, which misreports it.

    Ties go to the earlier weight, so the same inputs always produce the same
    split and a recalculation does not churn the ledger.

    A `total` of None means the source cost is unknown, and every share of an
    unknown is unknown too — never zero.
    """
    weights = [Decimal(weight) for weight in weights]
    if any(weight < 0 for weight in weights):
        raise ValidationError({'weights': 'A share weight cannot be negative.'})
    basis = sum(weights, Decimal('0'))
    if basis <= 0:
        raise ValidationError({'weights': 'At least one share must be positive.'})
    if total is None:
        return [None] * len(weights)
    total = Decimal(total).quantize(quantum, rounding=ROUND_HALF_UP)
    exact = [total * weight / basis for weight in weights]
    parts = [value.quantize(quantum, rounding=ROUND_DOWN) for value in exact]
    steps = int((total - sum(parts, Decimal('0'))) / quantum)
    order = sorted(
        range(len(weights)),
        key=lambda index: (parts[index] - exact[index], index),
    )
    for index in order[:steps]:
        parts[index] += quantum
    return parts


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


def physical_balances(lots):
    """Derive every lot/location balance for a set of lots in one query.

    The batched sibling of `physical_balance`, for callers that would
    otherwise run one aggregate per row they return. The lot alone answers
    the question — a movement belongs to exactly one lot, and a lot to one
    workspace — so the caller's workspace filter is already accounted for by
    the lots it passes in.

    The mapping carries an entry for every location a lot's history touched,
    including the ones it has since emptied, because a place stock has left
    is reported as zero rather than omitted. Pairs the ledger never touched
    read as zero too.

    Deliberately no stored total: a running balance kept beside the movement
    rows is a second source of truth that can drift from them, which is the
    thing an append-only ledger exists to prevent.
    """
    balances = defaultdict(Decimal)
    # The trailing `order_by()` clears the model's default ordering, which
    # would otherwise join `occurred_at` to the GROUP BY and hand back one
    # group per movement instead of one per lot and place.
    grouped = StockMovement.objects.filter(
        lot_id__in=[lot.pk for lot in lots],
    ).values(
        'lot_id', 'source_id', 'destination_id',
    ).annotate(total=Sum('quantity')).order_by()
    for row in grouped:
        if row['source_id']:
            balances[(row['lot_id'], row['source_id'])] -= row['total']
        if row['destination_id']:
            balances[(row['lot_id'], row['destination_id'])] += row['total']
    return balances


def bulk_balance(lot, location):
    """Derive how much of a lot is still anonymous bulk at one location.

    Numbering a unit posts nothing to the ledger, so `physical_balance` keeps
    counting it — correctly, because the pot is still on hand and still cost
    what it cost. What individualising changes is how much is left to sell or
    consume *as bulk*, and that is this figure: everything the lot has here,
    less the units drawn from it that are standing here in their own right.

    The subtraction stays right as stock moves, because units and bulk draw
    down the same lot. Selling a numbered pot posts a unit `SALE` that lowers
    `physical_balance` and deactivates the unit, so both sides fall together
    and the bulk figure does not move.
    """
    units = InventoryUnit.objects.filter(
        source_lot=lot,
        current_location=location,
        active=True,
    ).count()
    return physical_balance(lot, location) - Decimal(units)


def promised_bulk(lot, location):
    """Return how much anonymous stock a live sales reservation holds here.

    Numbering units and selling by the count draw on the very same pots, so
    neither may keep its own notion of what is free. Sales is built on the
    ledger rather than the other way round, so the import is deferred exactly
    as `unit_is_in_use` defers its reach into plantings.

    Only a reserved allocation counts. A pending one is a tentative selection
    somebody is still drafting, which warns rather than blocks, the same way it
    does for a plant.
    """
    from sales.models import SalesOrderAllocation  # pylint: disable=import-outside-toplevel

    total = SalesOrderAllocation.objects.filter(
        stock_lot=lot,
        source_location=location,
        status=SalesOrderAllocation.Status.RESERVED,
    ).aggregate(total=Sum('quantity'))['total']
    return Decimal(total or 0)


def unpromised_bulk(lot, location):
    """Return anonymous stock on hand that nothing has been promised out of.

    This, not `bulk_balance`, is what a new claim on the pool is measured
    against, and it is only trustworthy while the caller holds the lot lock
    `lock_lots` takes. Dispatching stock is deliberately *not* held to it: a
    fulfillment consumes the reservation it is drawing on, so measuring it
    against a figure that already excludes that reservation would have it
    refuse its own promise.
    """
    return bulk_balance(lot, location) - promised_bulk(lot, location)


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


def balance_is_known(lot):
    """Return whether this lot's balance is a figure worth enforcing.

    Sowing already depends on the answer being no for an unopened packet: the
    packet is truthfully sowable and its container goes negative as seed comes
    out of it. Anything that has to take stock back out of such a lot cannot be
    held to a figure that was never known in the first place.
    """
    return lot.quantity_certainty != QuantityCertainty.UNKNOWN


def _validate_source_balance(lot, source, quantity, unit=None):
    """Reject an outbound effect that exceeds physical stock at its source.

    Bulk stock leaving a mixed lot is held to the *bulk* figure, not the whole
    balance. The numbered pots are on hand and count towards `physical_balance`,
    but they are not available to a sale that ships anonymous ones — without
    this a sale of fifty could quietly draw down six that are standing on a
    bench with codes on them.

    A movement carrying a unit is exempt: that unit is itself the thing being
    removed, and it is by definition present.
    """
    mixed = lot.item.tracking_mode == InventoryItem.TrackingMode.MIXED
    if unit is None and mixed:
        available = bulk_balance(lot, source)
    else:
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
        _validate_source_balance(entry.lot, entry.source, quantity, entry.unit)
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
    _validate_reversible(original, reason)
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


def _receipt_acquisition_cost(line):
    """Return the explicit frozen acquisition amount on a receipt line."""
    return line.acquisition_amount.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _serialized_unit_costs(total, quantity):
    """Split a receipt total evenly across its units, losing no cent."""
    return distribute_exactly(total, [Decimal('1')] * int(quantity))


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


def _individualized_unit_costs(lot, count):
    """Split the cost of the pots being numbered across their new identities.

    Deliberately `base_unit_cost * count`, never the lot's acquisition total:
    most of that total belongs to the bulk still sitting in the box. An
    unpriced lot yields unpriced units, which is the same answer a receipt
    with no cost already gives them.
    """
    if lot.base_unit_cost is None:
        return [None] * count
    total = quantize_money(lot.base_unit_cost * count)
    return distribute_exactly(total, [Decimal('1')] * count)


@transaction.atomic
def individualize_lot_units(workspace, user, request):
    """Give individual identities to part of a mixed lot's bulk stock.

    This posts nothing to the ledger. Nothing entered or left the nursery, so
    `physical_balance` is unchanged and correct; what changes is how much of
    the lot is still anonymous, and `bulk_balance` derives that from the units
    themselves. The units are the whole record of the act.

    Numbering is one-way. A numbered pot leaves stock by being sold, wasted or
    lost, like any other asset, and never dissolves back into the bulk pool.
    """
    lot = lock_lots(workspace, [request.lot.pk])[request.lot.pk]
    count = int(request.count)
    if count < 1:
        raise ValidationError({'count': 'Number at least one unit.'})
    if lot.item.tracking_mode != InventoryItem.TrackingMode.MIXED:
        raise ValidationError({
            'lot': 'Only mixed-tracking stock can be individually numbered.',
        })
    if not lot.item.active:
        raise ValidationError({'lot': 'The item is inactive.'})
    _validate_location(request.location, workspace, 'location')
    if not balance_is_known(lot):
        raise ValidationError({
            'lot': 'Number units only from a lot whose quantity is known.',
        })
    available = unpromised_bulk(lot, request.location)
    if count > available:
        raise ValidationError({
            'count': (
                f'Only {available:.9f} {lot.item.base_unit} is unnumbered '
                f'and unpromised at {request.location.name}.'
            ),
        })
    return [
        InventoryUnit.objects.create(
            workspace=workspace,
            item=lot.item,
            source_lot=lot,
            acquisition_cost=cost,
            currency_code=lot.currency_code,
            current_location=request.location,
            created_by=user,
        )
        for cost in _individualized_unit_costs(lot, count)
    ]


def _numbering_is_unused(unit):
    """Return why a numbered unit is not safe to discard, or None."""
    if unit.movements.exists():
        return 'The unit has stock history.'
    if unit_is_in_use(unit):
        return 'The unit is holding a plant.'
    # Reached from function bodies so `inventory` keeps depending only on
    # `locations` and `workspaces` at import time, the same one-way-at-load
    # pattern `unit_is_in_use` already uses.
    from django.contrib.contenttypes.models import ContentType  # pylint: disable=import-outside-toplevel
    from labels.models import LabelPrintItem  # pylint: disable=import-outside-toplevel
    from sales.models import SalesOrderAllocation  # pylint: disable=import-outside-toplevel

    # Every numbered unit is issued an identity the moment it exists, so the
    # question is not whether it has a code but whether that code has been put
    # on anything. A printed label loose in the nursery would resolve to a pot
    # that no longer exists.
    printed = LabelPrintItem.objects.filter(
        identity__workspace=unit.workspace,
        identity__target_content_type=ContentType.objects.get_for_model(InventoryUnit),
        identity__target_object_id=unit.pk,
    ).exists()
    if printed:
        return 'A label for the unit has already been printed.'
    if SalesOrderAllocation.objects.filter(inventory_unit=unit).exists():
        return 'The unit is promised to an order.'
    return None


@transaction.atomic
def discard_numbering(workspace, unit):
    """Undo a numbering that was a typo, returning the pots to the bulk count.

    Numbering is one-way for stock that has been used: a pot that has moved,
    been labelled or been promised keeps its identity forever. But numbering
    posts no movement, so a unit that has done none of those things has left
    no trace to unwind, and `bulk_balance` counts it back the moment the row
    is gone. Without this, typing sixty instead of six is a write-off rather
    than a correction.
    """
    unit = lock_units(workspace, [unit.pk])[unit.pk]
    if unit.item.tracking_mode != InventoryItem.TrackingMode.MIXED:
        raise ValidationError({
            'unit': 'Only individually numbered mixed stock can be discarded.',
        })
    reason = _numbering_is_unused(unit)
    if reason is not None:
        raise ValidationError({'unit': reason})
    # Deliberately through the queryset: `InventoryUnit.delete` refuses, and
    # this is the one audited path allowed past it.
    InventoryUnit.objects.filter(pk=unit.pk).delete()


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
            if line.destination.code == 'SYSTEM-TRAY-UNKNOWN':
                raise ValidationError({
                    'lines': 'The unknown tray location is reserved for migration.',
                })

    posted_at = timezone.now()
    lots = []
    for line in lines:
        acquisition_total = _receipt_acquisition_cost(line)
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
                _create_seed_tray_for_unit(unit)
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


def _create_seed_tray_for_unit(unit):
    """Create a tray when the serialized item maps to tray geometry."""
    from seedtrays.services import create_tray_for_unit  # pylint: disable=import-outside-toplevel

    return create_tray_for_unit(unit)


def _linked_to_receipt(movement):
    """Return whether a receipt document posted this movement."""
    return bool(movement.receipt_line_id)


def _linked_to_stocktake(movement):
    """Return whether a stocktake document posted this movement."""
    return bool(movement.stocktake_line_id)


def _linked_to_application(movement):
    """Return whether an input application posted this movement.

    An application line owns the link, so this reads the reverse accessors
    rather than a column here. That keeps the dependency pointing one way: the
    apps that consume stock import inventory, and inventory imports none of
    them.
    """
    return hasattr(movement, 'application_consumption') or hasattr(movement, 'application_waste')


def _linked_to_tray_generation(movement):
    """Return whether cleaning a seed-tray generation posted this movement."""
    return hasattr(movement, 'tray_generation_residual')


#: Documents that own the movements they post, keyed by the name a caller passes
#: as ``document_kind``. A row one of these wrote may only be reversed through
#: its own document, so the document restores every row it posted together
#: instead of leaving some of them stranded.
DOCUMENT_LINKS = {
    'receipt': (
        _linked_to_receipt,
        'Reverse receipt movements through their receipt.',
    ),
    'stocktake': (
        _linked_to_stocktake,
        'Reverse stocktake movements through their stocktake.',
    ),
    'application': (
        _linked_to_application,
        'Reverse application movements through their application.',
    ),
    'tray_generation': (
        _linked_to_tray_generation,
        'Reverse tray-generation movements through their generation.',
    ),
}


def _validate_reversible(original, reason, document_kind=None):
    """Validate one original before any reversal row is written.

    ``document_kind`` names the document doing the reversing, which may reverse
    its own rows. Rows belonging to any other document stay refused, and a
    standalone reversal passes no kind at all.
    """
    if not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})
    if original.movement_type == StockMovement.MovementType.REVERSAL:
        raise ValidationError({'movement': 'A reversal cannot itself be reversed.'})
    if hasattr(original, 'reversal'):
        raise ValidationError({'movement': 'This movement is already reversed.'})
    for kind, (is_linked, message) in DOCUMENT_LINKS.items():
        if kind != document_kind and is_linked(original):
            raise ValidationError({'movement': message})
    if original.destination_id and balance_is_known(original.lot):
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
        enforce_source_balance=balance_is_known(original.lot),
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
    _validate_reversible(original, reason)
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
        _validate_reversible(original, reason, document_kind)
    return [
        _create_reversal(original, user, reason, occurred_at)
        for original in movements
    ]


def unit_is_in_use(unit):
    """Return whether cultivation still occupies this physical asset."""
    from plantings.models import SeedTrayPlanting, SpecificPlantLocation  # pylint: disable=import-outside-toplevel

    try:
        tray = unit.seed_tray
    except ObjectDoesNotExist:
        return SpecificPlantLocation.objects.filter(
            container_unit=unit,
            ended__isnull=True,
        ).exists()

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


def _check_unit_destination_capacity(unit, destination, reason):
    """Refuse to put a tray somewhere that has no room left for it.

    Only trays occupy growing space; any other serialized asset simply sits
    where it is put. The plants riding in the tray count too, because a bench
    measured in plants is just as full whether they arrived loose or in a tray.

    An overrun is allowed when the caller gave a reason, which the movement
    already records — that is the audited override, not a separate field.
    """
    from locations.occupancy import (  # pylint: disable=import-outside-toplevel
        check_capacity,
        container_contribution,
        tray_contribution,
    )
    from plantings.models import SpecificPlantLocation  # pylint: disable=import-outside-toplevel

    try:
        tray = unit.seed_tray
    except ObjectDoesNotExist:
        # A numbered pot takes up room too, and so do the plants in it. Before
        # pots could hold anything this returned here, which would now let any
        # number of them onto a bench measured in containers.
        if unit.item.tracking_mode != InventoryItem.TrackingMode.MIXED:
            return
        riding = SpecificPlantLocation.objects.filter(
            container_unit=unit,
            ended__isnull=True,
        ).count()
        check_capacity(destination, container_contribution(riding), reason)
        return

    riding = SpecificPlantLocation.objects.filter(
        seed_tray_cell__tray=tray,
        ended__isnull=True,
    ).count()
    check_capacity(destination, tray_contribution(riding), reason)


def _riders_travel(unit, movement_type):
    """Return whether this asset's plants leave with it.

    A numbered pot is sold with what is growing in it, so a sale is not a
    reason to refuse. A tray is lent rather than sold with its contents, and
    waste or loss is nobody's idea of a plan for a living plant, so both stay
    refused.
    """
    if movement_type != StockMovement.MovementType.SALE:
        return False
    try:
        unit.seed_tray
    except ObjectDoesNotExist:
        return True
    return False


@transaction.atomic
def post_unit_movement(workspace, user, request):  # pylint: disable=too-many-branches
    """Post one physical action against an exact locked serialized unit."""
    lot = lock_lots(workspace, [request.unit.source_lot_id])[
        request.unit.source_lot_id
    ]
    unit = lock_units(workspace, [request.unit.pk])[request.unit.pk]
    if not unit.item.active:
        raise ValidationError({'unit': 'The serialized item is inactive.'})
    if request.destination:
        _validate_location(request.destination, workspace, 'destination')
        if request.destination.code == 'SYSTEM-TRAY-UNKNOWN':
            raise ValidationError({
                'destination': 'The unknown tray location is reserved for migration.',
            })
    state = unit_physical_state(unit)
    source = unit.current_location
    destination = request.destination
    if source and source.code == 'SYSTEM-TRAY-UNKNOWN':
        raise ValidationError({
            'unit': 'Reconcile this opening unit before another stock action.',
        })
    allowed = {
        StockMovement.MovementType.TRANSFER,
        StockMovement.MovementType.ADJUSTMENT_LOSS,
        StockMovement.MovementType.WASTE,
        StockMovement.MovementType.ADJUSTMENT_GAIN,
        StockMovement.MovementType.SALE,
        StockMovement.MovementType.CUSTOMER_RETURN,
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
        StockMovement.MovementType.SALE,
    }:
        if not source:
            raise ValidationError({'unit': 'The unit is not currently on hand.'})
        if unit_is_in_use(unit) and not _riders_travel(unit, request.movement_type):
            raise ValidationError({
                'unit': 'Move or dispose of active plants before removing this asset.',
            })
        destination = None
    elif request.movement_type == StockMovement.MovementType.ADJUSTMENT_GAIN:
        if state not in {'lost', 'retired'}:
            raise ValidationError({'unit': 'Only a lost or retired unit can be returned.'})
        if not destination:
            raise ValidationError({'destination': 'A return destination is required.'})
        source = None
    else:
        if state != 'dispatched':
            raise ValidationError({'unit': 'Only a dispatched unit can be returned.'})
        if not destination:
            raise ValidationError({'destination': 'A customer return destination is required.'})
        source = None
    if destination is not None:
        _check_unit_destination_capacity(unit, destination, request.reason)
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
def settle_receipt(receipt, settled_on):
    """Record the date a posted receipt's supplier was paid, or clear it.

    Under the payments and hybrid bases this date is when the input tax on the
    receipt falls due, so it cannot be part of the document: a supplier is paid
    after the goods arrive, and `StockReceipt.save` refuses every change to a
    posted receipt. Writing through the queryset is how `reverse_receipt`
    already records a post-posting fact, and this follows it.

    Nothing about the lots moves. Acquisition cost was fixed when the receipt
    was posted, and paying for stock does not change what it cost.
    """
    receipt = StockReceipt.objects.select_for_update().select_related(
        'workspace',
    ).get(pk=receipt.pk)
    if receipt.status != StockReceipt.Status.POSTED:
        raise ValidationError({'status': 'Only posted receipts can be settled.'})
    from purchasing.models import SupplierInvoiceLine  # pylint: disable=import-outside-toplevel
    if SupplierInvoiceLine.objects.filter(receipt_line__receipt=receipt).exists():
        raise ValidationError({
            'settled_on': 'Record payments against the linked supplier invoice.',
        })
    if settled_on is not None:
        zone = ZoneInfo(receipt.workspace.timezone)
        if settled_on > timezone.now().astimezone(zone).date():
            raise ValidationError({
                'settled_on': 'A settlement date cannot be in the future.',
            })
    StockReceipt.objects.filter(pk=receipt.pk).update(
        settled_on=settled_on,
        updated=timezone.now(),
    )
    receipt.refresh_from_db()
    return receipt


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
        # A mixed lot's numbered pots are counted as their own identities, so
        # a loose count is measured against the bulk figure alone.
        if line.lot.item.tracking_mode == InventoryItem.TrackingMode.MIXED:
            expected = quantize_quantity(bulk_balance(line.lot, line.location))
        else:
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
def reverse_application_movements(workspace, movements, user, reason):
    """Restore the stock one input application consumed and wasted.

    The document itself lives outside this app, so it hands its movements in
    and keeps ownership of its own status. Locking and per-row validation are
    shared with receipts and stocktakes, which is what makes a partly reversed
    document impossible: every row is checked before any reversal is written.
    """
    return _reverse_document_movements(workspace, movements, user, reason, 'application')


@transaction.atomic
def reverse_tray_generation_movements(workspace, movements, user, reason):
    """Take back the stock a mistaken tray clean recovered.

    Cleaning a generation only moves stock for a remainder the operator put
    physically back on the shelf. Correcting that clean has to take those exact
    quantities out again, which is the same all-or-nothing document reversal
    receipts, stocktakes, and applications already share.
    """
    return _reverse_document_movements(workspace, movements, user, reason, 'tray_generation')


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
