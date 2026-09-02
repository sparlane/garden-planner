"""Atomic fulfillment, payment, return, refund, and reversal commands."""

# The services coordinate several ledgers deliberately in one transaction.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
# pylint: disable=too-many-arguments

import hashlib
import json
from decimal import Decimal
from uuid import uuid5

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from costing.services import plant_cost_breakdown
from health.operations import act_on_quarantine, quarantine_observation
from health.services import preview_observation, record_observation
from inventory.ledger import (
    MovementRequest,
    UnitMovementRequest,
    lock_lots,
    lock_units,
    post_stock_movement,
    post_unit_movement,
    reverse_movement,
)
from inventory.models import InventoryItem, StockMovement
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from plantings.models import SpecificPlant, SpecificPlantLocation

from .calculations import line_position_amounts, money, proportional_refund
from .models import (
    Fulfillment,
    FulfillmentLine,
    FulfillmentNumberSequence,
    FulfillmentPackagingLine,
    FulfillmentRider,
    Payment,
    Refund,
    RefundLine,
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesReturn,
    SalesReturnLine,
)


def request_fingerprint(values):
    """Return a stable digest so one operation key cannot mean two requests."""
    encoded = json.dumps(values, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _existing(model, workspace, operation_key, fingerprint):
    row = model.objects.filter(
        workspace=workspace, operation_key=operation_key,
    ).first()
    if row and row.request_fingerprint != fingerprint:
        raise ValidationError({
            'operation_key': 'That operation key was already used for different work.',
        })
    return row


def _number(workspace):
    sequence, _created = FulfillmentNumberSequence.objects.select_for_update(
        of=('self',),
    ).get_or_create(
        workspace=workspace,
    )
    number = sequence.next_number
    sequence.next_number += 1
    sequence.save(update_fields=['next_number'])
    return f'FUL-{number:06d}'


def _effective(queryset):
    return queryset.filter(reversal_of__isnull=True, reversal__isnull=True)


def _effective_fulfillment_lines(order):
    return FulfillmentLine.objects.filter(
        fulfillment__order=order,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related('allocation__line', 'fulfillment')


def _effective_return_line_ids(order):
    return set(SalesReturnLine.objects.filter(
        sales_return__order=order,
        sales_return__reversal_of__isnull=True,
        sales_return__reversal__isnull=True,
    ).values_list('fulfillment_line_id', flat=True))


def dispatched_quantity(allocation):
    """Return how many of a line's units one allocation ships.

    An identity is exactly one, which is why `quantity` is null on it rather
    than stored as a one nothing may contradict. Deriving it here rather than
    snapshotting a column on `FulfillmentLine` keeps the promise and the
    dispatch the same figure.
    """
    return 1 if allocation.quantity is None else allocation.quantity


def recompute_order_status(order):
    """Derive fulfillment status from effective dispatch and return facts."""
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    if order.status == SalesOrder.Status.CANCELLED:
        return order
    returned = _effective_return_line_ids(order)
    fulfilled = sum(
        dispatched_quantity(row.allocation)
        for row in _effective_fulfillment_lines(order).exclude(pk__in=returned)
    )
    requested = sum(order.lines.values_list('quantity', flat=True))
    if fulfilled == 0:
        next_status = SalesOrder.Status.CONFIRMED
    elif fulfilled < requested:
        next_status = SalesOrder.Status.PARTIALLY_FULFILLED
    else:
        next_status = SalesOrder.Status.FULFILLED
    SalesOrder.objects.filter(pk=order.pk).update(
        status=next_status, updated=timezone.now(),
    )
    order.status = next_status
    return order


def _occupied_positions(row):
    """Return every commercial position one dispatched line already holds.

    A counted dispatch takes a contiguous run rather than a single slot, so
    the run is derived from where it started and how many it shipped.
    """
    start = row.commercial_position
    return set(range(start, start + dispatched_quantity(row.allocation)))


def _available_positions(order):
    returned = _effective_return_line_ids(order)
    occupied = {}
    for row in _effective_fulfillment_lines(order).exclude(pk__in=returned):
        occupied.setdefault(row.allocation.line_id, set()).update(
            _occupied_positions(row),
        )
    return {
        line.pk: [
            position for position in range(1, line.quantity + 1)
            if position not in occupied.get(line.pk, set())
        ]
        for line in order.lines.all()
    }


def _take_positions(available, needed):
    """Remove and return a contiguous run of free positions, or None.

    Contiguous because the run is stored as its first position plus a count.
    Scanning rather than always taking the front matters once something has
    been returned: a whole-allocation return frees the exact block it shipped,
    which can leave a hole a later dispatch of the right size still fits.
    """
    for index in range(len(available) - needed + 1):
        block = available[index:index + needed]
        if block[-1] - block[0] == needed - 1:
            del available[index:index + needed]
            return block
    return None


def _position_amounts(line, positions):
    """Add up the commercial amounts of every position one dispatch covers."""
    per_position = line_position_amounts(line)
    fields = ('gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax')
    return {
        field: money(sum(per_position[position][field] for position in positions))
        for field in fields
    }


def _plant_cost(plant):
    breakdown = plant_cost_breakdown(plant)
    value = breakdown['provisional_value'] or breakdown['final_value']
    return (
        Decimal(value) if value is not None else None,
        bool(breakdown['provisional']),
    )


def _resolve_riders(units, selected_plant_ids):
    """Decide, per unit, what happens to the plants it is carrying.

    A tray is a container being lent, so it may not go out holding plants
    nobody sold: the fulfillment has to name them. A numbered pot is sold with
    what is growing in it, so its plants come along and are returned as part of
    the same line rather than needing a second one.
    """
    riders = {}
    for unit in units.values():
        try:
            tray = unit.seed_tray
        except ObjectDoesNotExist:
            riders[unit.pk] = list(SpecificPlantLocation.objects.filter(
                container_unit=unit, ended__isnull=True,
            ).select_related('specific_plant__batch'))
            continue
        carried = set(SpecificPlantLocation.objects.filter(
            seed_tray_cell__tray=tray, ended__isnull=True,
        ).values_list('specific_plant_id', flat=True))
        if not carried.issubset(selected_plant_ids):
            raise ValidationError({
                'allocations': f'Tray {tray.pk} still carries plants not in this fulfillment.',
            })
        riders[unit.pk] = []
    return riders


def _validate_riders_are_free(riders, order):
    """Refuse to sell a pot whose plants are already promised elsewhere.

    A potted specimen is sellable exactly once. Without this the same plant
    could go out on its own seedling line and again inside its container.
    """
    plant_ids = [
        placement.specific_plant_id
        for placements in riders.values()
        for placement in placements
    ]
    if not plant_ids:
        return
    claimed = SalesOrderAllocation.objects.filter(
        plant_id__in=plant_ids,
        status__in=(
            SalesOrderAllocation.Status.RESERVED,
            SalesOrderAllocation.Status.FULFILLED,
        ),
    ).exclude(line__order=order).values_list('plant_id', flat=True)
    duplicated = sorted(set(claimed))
    if duplicated:
        raise ValidationError({
            'allocations': (
                f'Plants {duplicated} are promised on another order and cannot '
                'be sold inside a container.'
            ),
        })


def _sell_rider(line, placement, user, fulfilled_at, cogs_amount):
    """Record one plant as sold because the container holding it was.

    The plant's placement ends here rather than following the pot: once the
    container has left, saying the plant is still standing in it would be a
    claim about somebody else's greenhouse.
    """
    plant = placement.specific_plant
    event = record_lifecycle_event(
        plant, user,
        OutcomeRequest(
            EventType.SOLD, occurred_at=fulfilled_at,
            reference=f'fulfillment:{line.fulfillment_id}:container:{line.pk}',
        ),
    )
    SpecificPlantLocation.objects.filter(pk=placement.pk).update(ended=fulfilled_at)
    return FulfillmentRider.objects.create(
        fulfillment_line=line,
        plant=plant,
        lifecycle_event=event,
        cogs_amount=cogs_amount,
    )


def _return_riders(line, sales_return, user, *, returned_at, reason, outcome):
    """Bring a returned container's plants back with it.

    They went out as passengers on this line, so they come back on it too,
    landing in the pot again unless it is being discarded. Returns the plants
    needing quarantine, which the caller handles for the whole document at
    once.
    """
    quarantined = []
    for rider in line.riders.select_related('plant').all():
        event = record_lifecycle_event(
            rider.plant, user,
            OutcomeRequest(
                _return_event(outcome), occurred_at=returned_at, reason=reason,
                reference=f'return:{sales_return.pk}:container:{line.pk}',
            ),
        )
        FulfillmentRider.objects.filter(pk=rider.pk).update(return_event=event)
        if outcome != SalesReturnLine.Outcome.DISCARDED:
            SpecificPlantLocation.objects.create(
                specific_plant=rider.plant,
                location_type=SpecificPlantLocation.CONTAINER_UNIT,
                container_unit=line.allocation.inventory_unit,
                started=returned_at,
                notes=reason,
            )
        if outcome == SalesReturnLine.Outcome.QUARANTINED:
            quarantined.append(rider.plant)
    return quarantined


def _dispatch_counted_stock(order, user, allocation, lot, *, fulfillment, fulfilled_at):
    """Ship anonymous stock by the count and value it from its own lot.

    One `SALE` movement for the whole allocation, not one per pot: the stock
    left as a stack and the ledger says so. An unpriced lot yields an unknown
    cost rather than a zero, and marks the line provisional so the figure is
    read as one still waiting for a price.
    """
    movement = post_stock_movement(
        order.workspace, user,
        MovementRequest(
            lot=lot, movement_type=StockMovement.MovementType.SALE,
            quantity=Decimal(allocation.quantity),
            source=allocation.source_location,
            occurred_at=fulfilled_at, reason='Order fulfillment',
            reference=f'fulfillment:{fulfillment.pk}:allocation:{allocation.pk}',
        ),
    )
    if lot.base_unit_cost is None:
        return movement, None, True
    return movement, money(Decimal(allocation.quantity) * lot.base_unit_cost), False


@transaction.atomic
def post_fulfillment(order, user, *, operation_key, allocation_ids,
                     packaging=(), fulfilled_at=None, notes=''):
    """Dispatch exact reserved stock and recognize its revenue and direct cost."""
    requested_at = fulfilled_at
    fulfilled_at = fulfilled_at or timezone.now()
    payload = {
        'order': order.pk, 'allocations': sorted(set(allocation_ids)),
        'packaging': sorted(packaging, key=lambda row: (row['lot'].pk, row['source'].pk)),
        'fulfilled_at': requested_at, 'notes': notes,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(Fulfillment, order.workspace, operation_key, fingerprint)
    if existing:
        return existing
    order = SalesOrder.objects.select_for_update(
        of=('self',),
    ).prefetch_related('lines').get(pk=order.pk)
    if order.status not in {
            SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_FULFILLED}:
        raise ValidationError({'status': 'Only a confirmed incomplete order can be fulfilled.'})
    existing = _existing(Fulfillment, order.workspace, operation_key, fingerprint)
    if existing:
        return existing
    wanted = sorted(set(allocation_ids))
    if not wanted:
        raise ValidationError({'allocations': 'Select at least one reserved allocation.'})
    allocations = list(SalesOrderAllocation.objects.select_for_update(of=('self',)).select_related(
        'line', 'plant__batch', 'inventory_unit__item', 'stock_lot__item',
        'source_location',
    ).filter(line__order=order, pk__in=wanted).order_by('pk'))
    if len(allocations) != len(wanted) or any(
            row.status != SalesOrderAllocation.Status.RESERVED for row in allocations):
        raise ValidationError({'allocations': 'One or more active reservations are unavailable.'})
    plant_ids = [row.plant_id for row in allocations if row.plant_id]
    unit_ids = [row.inventory_unit_id for row in allocations if row.inventory_unit_id]
    plants = {
        row.pk: row for row in SpecificPlant.objects.select_for_update(of=('self',))
        .select_related('batch').filter(workspace=order.workspace, pk__in=plant_ids)
        .order_by('pk')
    }
    units = lock_units(order.workspace, unit_ids)
    # One lock covering both the packaging drawn down and the counted stock
    # dispatched, so a fulfillment cannot deadlock against its own two halves.
    lot_ids = [row['lot'].pk for row in packaging]
    lot_ids += [row.stock_lot_id for row in allocations if row.stock_lot_id]
    lots = lock_lots(order.workspace, lot_ids)
    riders = _resolve_riders(units, set(plant_ids))
    _validate_riders_are_free(riders, order)
    positions = _available_positions(order)
    fulfillment = Fulfillment.objects.create(
        workspace=order.workspace, order=order,
        fulfillment_number=_number(order.workspace), fulfilled_at=fulfilled_at,
        notes=notes.strip(), operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )
    for allocation in allocations:
        needed = dispatched_quantity(allocation)
        taken = _take_positions(positions[allocation.line_id], needed)
        if taken is None:
            raise ValidationError({'allocations': 'A line has no remaining quantity to fulfill.'})
        amounts = _position_amounts(allocation.line, taken)
        position = taken[0]
        lifecycle_event = None
        stock_movement = None
        if allocation.plant_id:
            plant = plants[allocation.plant_id]
            lifecycle_event = record_lifecycle_event(
                plant, user,
                OutcomeRequest(
                    EventType.SOLD, occurred_at=fulfilled_at,
                    reference=f'fulfillment:{fulfillment.pk}:allocation:{allocation.pk}',
                ),
            )
            cogs_amount, provisional = _plant_cost(plant)
        elif allocation.stock_lot_id:
            stock_movement, cogs_amount, provisional = _dispatch_counted_stock(
                order, user, allocation, lots[allocation.stock_lot_id],
                fulfillment=fulfillment, fulfilled_at=fulfilled_at,
            )
        else:
            unit = units[allocation.inventory_unit_id]
            stock_movement = post_unit_movement(
                order.workspace, user,
                UnitMovementRequest(
                    unit=unit, movement_type=StockMovement.MovementType.SALE,
                    occurred_at=fulfilled_at, reason='Order fulfillment',
                    reference=f'fulfillment:{fulfillment.pk}:allocation:{allocation.pk}',
                ),
            )
            cogs_amount, provisional = unit.acquisition_cost, False
        carried = riders.get(allocation.inventory_unit_id, [])
        rider_costs = [_plant_cost(row.specific_plant) for row in carried]
        if carried:
            # The pot's own cost is the small half of what went out the door.
            # Leaving the plants out would understate cost of sale on exactly
            # the specimens this line exists to sell.
            known = [amount for amount, _ in rider_costs if amount is not None]
            cogs_amount = (cogs_amount or Decimal('0')) + sum(known, Decimal('0'))
            provisional = provisional or any(flag for _, flag in rider_costs)
        line = FulfillmentLine.objects.create(
            fulfillment=fulfillment, allocation=allocation,
            commercial_position=position, cogs_amount=cogs_amount,
            cogs_provisional=provisional, currency_code=order.currency_code,
            tax_treatment=allocation.line.tax_treatment,
            lifecycle_event=lifecycle_event, stock_movement=stock_movement,
            **amounts,
        )
        for placement, (rider_cost, _flag) in zip(carried, rider_costs):
            _sell_rider(line, placement, user, fulfilled_at, rider_cost)
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
            status=SalesOrderAllocation.Status.FULFILLED, updated=timezone.now(),
        )
        ReservationEvent.objects.create(
            allocation=allocation, event_type=ReservationEvent.EventType.FULFILLED,
            occurred_at=fulfilled_at, created_by=_actor(user),
        )
    for item in packaging:
        lot = lots[item['lot'].pk]
        if lot.item.category != InventoryItem.Category.PACKAGING:
            raise ValidationError({'packaging': 'Choose packaging inventory lots only.'})
        movement = post_stock_movement(
            order.workspace, user,
            MovementRequest(
                lot=lot, movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=item['quantity'], source=item['source'],
                occurred_at=fulfilled_at, reason='Fulfillment packaging',
                reference=f'fulfillment:{fulfillment.pk}',
            ),
        )
        cost = None
        if lot.base_unit_cost is not None:
            cost = money(Decimal(item['quantity']) * lot.base_unit_cost)
        FulfillmentPackagingLine.objects.create(
            fulfillment=fulfillment, lot=lot, source=item['source'],
            quantity=item['quantity'], base_unit=lot.item.base_unit,
            unit_cost=lot.base_unit_cost, cogs_amount=cost,
            currency_code=lot.currency_code, stock_movement=movement,
        )
    recompute_order_status(order)
    return fulfillment


@transaction.atomic
def record_payment(order, user, *, operation_key, paid_on, amount, method,
                   external_reference='', account_reference='', notes=''):
    """Record operational cash independently from fulfillment timing."""
    amount = money(amount)
    payload = {
        'order': order.pk, 'paid_on': paid_on, 'amount': amount,
        'method': method, 'external_reference': external_reference,
        'account_reference': account_reference, 'notes': notes,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(Payment, order.workspace, operation_key, fingerprint)
    if existing:
        return existing
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    if order.status in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT, SalesOrder.Status.CANCELLED}:
        raise ValidationError({'status': 'Payments require an active confirmed order.'})
    return Payment.objects.create(
        workspace=order.workspace, order=order, paid_on=paid_on, amount=amount,
        currency_code=order.currency_code, method=method,
        external_reference=external_reference.strip(),
        account_reference=account_reference.strip(), notes=notes.strip(),
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )


def _return_event(outcome):
    return {
        SalesReturnLine.Outcome.AVAILABLE: EventType.RETURNED_AVAILABLE,
        SalesReturnLine.Outcome.QUARANTINED: EventType.RETURNED_QUARANTINED,
        SalesReturnLine.Outcome.DISCARDED: EventType.RETURNED_DISCARDED,
    }[outcome]


def _validate_whole_allocation_returns(items, lines):
    """Refuse a part-return of a counted dispatch, naming what it must be.

    A partial return would have to split one fulfillment line's recognised
    money and its cost of sale, which is precisely the rewrite task 114 exists
    to do; inventing it here would put a migration over posted money inside a
    feature. Refusing with the number in hand beats silently returning the lot.
    """
    for item in items:
        line = lines[item['fulfillment_line'].pk]
        wanted = item.get('quantity')
        shipped = dispatched_quantity(line.allocation)
        if wanted is not None and wanted != shipped:
            raise ValidationError({
                'items': (
                    f'Fulfillment line {line.pk} shipped {shipped} and can only '
                    'be returned whole.'
                ),
            })


def _return_counted_stock(order, user, line, sales_return, *, returned_at,
                          reason, outcome, destination):
    """Bring back a whole counted dispatch, and write off what is unsaleable.

    The stock comes back to where it was shipped from unless the operator
    named somewhere else, which is how a returned numbered unit behaves too. A
    discarded return lands first and is then wasted, so the ledger records both
    facts rather than quietly never taking the stock back.
    """
    allocation = line.allocation
    quantity = Decimal(allocation.quantity)
    lot = allocation.stock_lot
    return_movement = post_stock_movement(
        order.workspace, user,
        MovementRequest(
            lot=lot, movement_type=StockMovement.MovementType.CUSTOMER_RETURN,
            quantity=quantity,
            destination=destination or line.stock_movement.source,
            occurred_at=returned_at, reason=reason,
            reference=f'return:{sales_return.pk}:line:{line.pk}',
        ),
    )
    discard_movement = None
    if outcome == SalesReturnLine.Outcome.DISCARDED:
        discard_movement = post_stock_movement(
            order.workspace, user,
            MovementRequest(
                lot=lot, movement_type=StockMovement.MovementType.WASTE,
                quantity=quantity,
                source=destination or line.stock_movement.source,
                occurred_at=returned_at, reason=reason,
                reference=f'return:{sales_return.pk}:discard:{line.pk}',
            ),
        )
    return return_movement, discard_movement


@transaction.atomic
def post_return(order, user, *, operation_key, items, reason, returned_at=None,
                notes='', observation_type=None, severity=None,
                follow_up_due_at=None):
    """Return exact fulfilled stock with an explicit physical outcome."""
    requested_at = returned_at
    returned_at = returned_at or timezone.now()
    payload = {
        'order': order.pk, 'items': sorted(items, key=lambda row: row['fulfillment_line'].pk),
        'reason': reason, 'returned_at': requested_at, 'notes': notes,
        'observation_type': getattr(observation_type, 'pk', None),
        'severity': severity, 'follow_up_due_at': follow_up_due_at,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(SalesReturn, order.workspace, operation_key, fingerprint)
    if existing:
        return existing
    if not reason.strip() or not items:
        raise ValidationError({'reason': 'A reason and at least one returned item are required.'})
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    line_ids = [row['fulfillment_line'].pk for row in items]
    lines = {
        row.pk: row for row in FulfillmentLine.objects.select_for_update(of=('self',))
        .select_related('allocation__plant', 'allocation__inventory_unit',
                        'allocation__stock_lot__item', 'allocation__source_location')
        .filter(fulfillment__order=order, fulfillment__reversal__isnull=True,
                pk__in=line_ids).order_by('pk')
    }
    if len(lines) != len(set(line_ids)):
        raise ValidationError({'items': 'One or more fulfillment lines are unavailable.'})
    _validate_whole_allocation_returns(items, lines)
    lock_lots(order.workspace, [
        row.allocation.stock_lot_id for row in lines.values()
        if row.allocation.stock_lot_id
    ])
    already = _effective(SalesReturn.objects.filter(order=order)).filter(
        lines__fulfillment_line_id__in=line_ids,
    ).exists()
    if already:
        raise ValidationError({'items': 'One or more items were already returned.'})
    quarantined_plants = []
    sales_return = SalesReturn.objects.create(
        workspace=order.workspace, order=order, returned_at=returned_at,
        reason=reason.strip(), notes=notes.strip(), operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )
    for item in items:
        line = lines[item['fulfillment_line'].pk]
        allocation = line.allocation
        outcome = item['outcome']
        destination = item.get('destination')
        if outcome != SalesReturnLine.Outcome.DISCARDED and destination is None:
            raise ValidationError({'destination': 'Available and quarantined returns need a destination.'})
        lifecycle_event = None
        return_movement = None
        discard_movement = None
        if allocation.stock_lot_id:
            return_movement, discard_movement = _return_counted_stock(
                order, user, line, sales_return,
                returned_at=returned_at, reason=reason, outcome=outcome,
                destination=destination,
            )
        elif allocation.plant_id:
            lifecycle_event = record_lifecycle_event(
                allocation.plant, user,
                OutcomeRequest(
                    _return_event(outcome), occurred_at=returned_at, reason=reason,
                    reference=f'return:{sales_return.pk}:line:{line.pk}',
                ),
            )
            if destination:
                from plantings.rest import move_specific_plant  # pylint: disable=import-outside-toplevel
                move_specific_plant(allocation.plant, {
                    'location_type': SpecificPlantLocation.LOCATION,
                    'location': destination,
                    'started': returned_at,
                    'notes': reason,
                }, user=user)
            if outcome == SalesReturnLine.Outcome.QUARANTINED:
                quarantined_plants.append(allocation.plant)
        else:
            unit = allocation.inventory_unit
            return_destination = destination
            if return_destination is None:
                return_destination = line.stock_movement.source
            return_movement = post_unit_movement(
                order.workspace, user,
                UnitMovementRequest(
                    unit=unit,
                    movement_type=StockMovement.MovementType.CUSTOMER_RETURN,
                    destination=return_destination, occurred_at=returned_at,
                    reason=reason, reference=f'return:{sales_return.pk}:line:{line.pk}',
                ),
            )
            quarantined_plants.extend(_return_riders(
                line, sales_return, user,
                returned_at=returned_at, reason=reason, outcome=outcome,
            ))
            if outcome == SalesReturnLine.Outcome.DISCARDED:
                discard_movement = post_unit_movement(
                    order.workspace, user,
                    UnitMovementRequest(
                        unit=unit, movement_type=StockMovement.MovementType.WASTE,
                        occurred_at=returned_at, reason=reason,
                        reference=f'return:{sales_return.pk}:discard:{line.pk}',
                    ),
                )
        SalesReturnLine.objects.create(
            sales_return=sales_return, fulfillment_line=line, outcome=outcome,
            destination=destination, lifecycle_event=lifecycle_event,
            return_movement=return_movement, discard_movement=discard_movement,
        )
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
            status=SalesOrderAllocation.Status.RETURNED, updated=timezone.now(),
        )
    if quarantined_plants:
        if observation_type is None or severity is None:
            raise ValidationError({'health': 'Quarantined plants need an observation type and severity.'})
        scopes = [{'type': 'plant', 'id': plant.pk} for plant in quarantined_plants]
        preview = preview_observation(order.workspace, scopes)
        observation = record_observation(
            order.workspace, user, scopes=scopes, reviewed_digest=preview['digest'],
            observation_type=observation_type, severity=severity,
            occurred_at=returned_at, follow_up_due_at=follow_up_due_at,
            notes=reason,
        )
        case, _action = quarantine_observation(
            order.workspace, user, observation, idempotency_key=operation_key,
            reason=reason, occurred_at=returned_at,
        )
        SalesReturn.objects.filter(pk=sales_return.pk).update(
            health_observation=observation, quarantine_case=case,
        )
        sales_return.health_observation = observation
        sales_return.quarantine_case = case
    recompute_order_status(order)
    return sales_return


@transaction.atomic
def post_refund(order, user, *, operation_key, payment, fulfillment_lines,
                amount, reason, refunded_at=None, sales_return=None,
                account_reference='', notes=''):
    """Refund paid value and classify it against original recognized lines."""
    requested_at = refunded_at
    refunded_at = refunded_at or timezone.now()
    amount = money(amount)
    line_ids = sorted(set(line.pk for line in fulfillment_lines))
    payload = {
        'order': order.pk, 'payment': payment.pk, 'lines': line_ids,
        'amount': amount, 'reason': reason, 'refunded_at': requested_at,
        'sales_return': getattr(sales_return, 'pk', None),
        'account_reference': account_reference, 'notes': notes,
    }
    fingerprint = request_fingerprint(payload)
    existing = _existing(Refund, order.workspace, operation_key, fingerprint)
    if existing:
        return existing
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    payment = Payment.objects.select_for_update(of=('self',)).filter(
        pk=payment.pk, order=order, reversal__isnull=True, reversal_of__isnull=True,
    ).first()
    if payment is None:
        raise ValidationError({'payment': 'Choose an effective payment from this order.'})
    if sales_return and sales_return.order_id != order.pk:
        raise ValidationError({'sales_return': 'Choose a return from this order.'})
    lines = list(FulfillmentLine.objects.select_for_update(of=('self',)).filter(
        fulfillment__order=order, fulfillment__reversal__isnull=True,
        fulfillment__reversal_of__isnull=True, pk__in=line_ids,
    ).order_by('pk'))
    if len(lines) != len(line_ids) or not lines:
        raise ValidationError({'fulfillment_lines': 'Choose effective fulfilled items.'})
    paid_refunded = _effective(payment.refunds.all()).aggregate(
        total=Sum('amount'),
    )['total'] or Decimal('0')
    if amount > payment.amount - paid_refunded:
        raise ValidationError({'amount': 'The refund exceeds this payment balance.'})
    available = []
    for line in lines:
        prior = RefundLine.objects.filter(
            fulfillment_line=line,
            refund__reversal_of__isnull=True,
            refund__reversal__isnull=True,
        ).aggregate(total=Sum('total_incl_tax'))['total'] or Decimal('0')
        available.append({
            'line': line,
            'remaining_total': money(line.total_incl_tax - prior),
        })
    try:
        shares = proportional_refund(amount, available)
    except ValueError as exc:
        raise ValidationError({'amount': str(exc)}) from exc
    refund = Refund.objects.create(
        workspace=order.workspace, order=order, payment=payment,
        sales_return=sales_return, refunded_at=refunded_at, amount=amount,
        currency_code=order.currency_code, reason=reason.strip(), notes=notes.strip(),
        account_reference=account_reference.strip(),
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )
    RefundLine.objects.bulk_create([_refund_line(refund, share) for share in shares])
    return refund


def _refund_line(refund, share):
    """Build one refund line, carrying its source line's GST treatment.

    The treatment travels with the money so a credit lands in the same box of
    the return as the supply it reverses.
    """
    line = share.pop('line')
    return RefundLine(
        refund=refund, fulfillment_line=line,
        tax_treatment=line.tax_treatment, **share,
    )


def order_commerce_summary(order):
    """Return separate physical, revenue, refund, and cash totals."""
    fulfilled = _effective_fulfillment_lines(order)
    returned_ids = _effective_return_line_ids(order)
    refunds = _effective(order.refunds.all())
    payments = _effective(order.payments.all())
    fulfilled_total = fulfilled.aggregate(total=Sum('total_incl_tax'))['total'] or 0
    refunded_total = refunds.aggregate(total=Sum('amount'))['total'] or 0
    paid_total = payments.aggregate(total=Sum('amount'))['total'] or 0
    net_order = money(order.total_incl_tax - refunded_total)
    net_paid = money(paid_total - refunded_total)
    outstanding = money(max(net_order - net_paid, Decimal('0')))
    overpaid = money(max(net_paid - net_order, Decimal('0')))
    if net_paid == 0:
        payment_status = 'unpaid'
    elif net_paid < net_order:
        payment_status = 'partially_paid'
    elif net_paid == net_order:
        payment_status = 'paid'
    else:
        payment_status = 'overpaid'
    return {
        'requested_quantity': sum(order.lines.values_list('quantity', flat=True)),
        'reserved_quantity': order.lines.filter(
            allocations__status=SalesOrderAllocation.Status.RESERVED,
        ).count(),
        'fulfilled_quantity': fulfilled.count(),
        'returned_quantity': len(returned_ids),
        'fulfilled_total_incl_tax': f'{money(fulfilled_total):f}',
        'refunded_total_incl_tax': f'{money(refunded_total):f}',
        'paid_total': f'{money(paid_total):f}',
        'net_paid_total': f'{net_paid:f}',
        'outstanding_total': f'{outstanding:f}',
        'overpaid_total': f'{overpaid:f}',
        'payment_status': payment_status,
        'currency_code': order.currency_code,
    }


def _refuse_reversed(original, field, label):
    """Refuse a second reversal, and a reversal of a reversal.

    A reversal is an ordinary document on the order, so the route that finds
    the original finds it too. Locking with `reversal_of__isnull=True` used to
    filter it out here, which raised `DoesNotExist` and reached the client as a
    server error instead of a field error explaining the problem.
    """
    if original.reversal_of_id is not None:
        raise ValidationError({field: f'A {label} reversal cannot itself be reversed.'})
    if hasattr(original, 'reversal'):
        raise ValidationError({field: f'This {label} is already reversed.'})


@transaction.atomic
def reverse_fulfillment(original, user, *, operation_key, reason, occurred_at=None):
    """Append a fulfillment reversal and restore its exact reservations."""
    requested_at = occurred_at
    occurred_at = occurred_at or timezone.now()
    payload = {'original': original.pk, 'reason': reason, 'occurred_at': requested_at}
    fingerprint = request_fingerprint(payload)
    existing = _existing(Fulfillment, original.workspace, operation_key, fingerprint)
    if existing:
        return existing
    original = Fulfillment.objects.select_for_update(of=('self',)).prefetch_related(
        'lines__allocation', 'packaging_lines',
    ).get(pk=original.pk)
    _refuse_reversed(original, 'fulfillment', 'fulfillment')
    if _effective(SalesReturn.objects.filter(
            lines__fulfillment_line__fulfillment=original)).exists():
        raise ValidationError({'fulfillment': 'Reverse linked returns first.'})
    if _effective(Refund.objects.filter(
            lines__fulfillment_line__fulfillment=original)).exists():
        raise ValidationError({'fulfillment': 'Reverse linked refunds first.'})
    reversal = Fulfillment.objects.create(
        workspace=original.workspace, order=original.order,
        fulfillment_number=_number(original.workspace), fulfilled_at=occurred_at,
        notes=reason.strip(), reversal_of=original, operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )
    for line in original.lines.all():
        if line.lifecycle_event_id:
            reverse_lifecycle_event(line.lifecycle_event, user, reason, occurred_at)
        if line.stock_movement_id:
            reverse_movement(line.stock_movement, user, reason, occurred_at)
        SalesOrderAllocation.objects.filter(pk=line.allocation_id).update(
            status=SalesOrderAllocation.Status.RESERVED, updated=timezone.now(),
        )
    for packaging in original.packaging_lines.all():
        reverse_movement(packaging.stock_movement, user, reason, occurred_at)
    recompute_order_status(original.order)
    return reversal


@transaction.atomic
def reverse_payment(original, user, *, operation_key, reason, occurred_at=None):
    """Append a payment reversal after all dependent refunds are reversed."""
    requested_at = occurred_at
    occurred_at = occurred_at or timezone.now()
    payload = {'original': original.pk, 'reason': reason, 'occurred_at': requested_at}
    fingerprint = request_fingerprint(payload)
    existing = _existing(Payment, original.workspace, operation_key, fingerprint)
    if existing:
        return existing
    original = Payment.objects.select_for_update(of=('self',)).get(pk=original.pk)
    _refuse_reversed(original, 'payment', 'payment')
    if _effective(original.refunds.all()).exists():
        raise ValidationError({'payment': 'Reverse linked refunds first.'})
    return Payment.objects.create(
        workspace=original.workspace, order=original.order,
        paid_on=occurred_at.date(), amount=original.amount,
        currency_code=original.currency_code, method=original.method,
        external_reference=original.external_reference, notes=reason.strip(),
        account_reference=original.account_reference,
        reversal_of=original, operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )


@transaction.atomic
def reverse_refund(original, user, *, operation_key, reason, occurred_at=None):
    """Append a monetary reversal without rewriting recognized refund rows."""
    requested_at = occurred_at
    occurred_at = occurred_at or timezone.now()
    payload = {'original': original.pk, 'reason': reason, 'occurred_at': requested_at}
    fingerprint = request_fingerprint(payload)
    existing = _existing(Refund, original.workspace, operation_key, fingerprint)
    if existing:
        return existing
    original = Refund.objects.select_for_update(of=('self',)).get(pk=original.pk)
    _refuse_reversed(original, 'refund', 'refund')
    return Refund.objects.create(
        workspace=original.workspace, order=original.order,
        payment=original.payment, sales_return=original.sales_return,
        refunded_at=occurred_at, amount=original.amount,
        currency_code=original.currency_code, reason=reason.strip(),
        account_reference=original.account_reference,
        reversal_of=original, operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )


@transaction.atomic
def reverse_return(original, user, *, operation_key, reason, occurred_at=None):
    """Append a physical-return reversal if the stock has not moved on."""
    requested_at = occurred_at
    occurred_at = occurred_at or timezone.now()
    payload = {'original': original.pk, 'reason': reason, 'occurred_at': requested_at}
    fingerprint = request_fingerprint(payload)
    existing = _existing(SalesReturn, original.workspace, operation_key, fingerprint)
    if existing:
        return existing
    original = SalesReturn.objects.select_for_update(of=('self',)).prefetch_related(
        'lines__fulfillment_line__allocation',
    ).get(pk=original.pk)
    _refuse_reversed(original, 'sales_return', 'return')
    if _effective(original.refunds.all()).exists():
        raise ValidationError({'sales_return': 'Reverse linked refunds first.'})
    targets = []
    for line in original.lines.all():
        allocation = line.fulfillment_line.allocation
        target_filter = {'plant_id': allocation.plant_id} if allocation.plant_id else {
            'inventory_unit_id': allocation.inventory_unit_id,
        }
        moved_on = SalesOrderAllocation.objects.filter(
            **target_filter,
            status__in=[SalesOrderAllocation.Status.RESERVED,
                        SalesOrderAllocation.Status.FULFILLED],
        ).exclude(pk=allocation.pk).exists()
        if moved_on:
            targets.append(allocation.pk)
    if targets:
        raise ValidationError({'sales_return': 'Returned stock has already been reallocated.'})
    reversal = SalesReturn.objects.create(
        workspace=original.workspace, order=original.order,
        returned_at=occurred_at, reason=reason.strip(), reversal_of=original,
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )
    for line in original.lines.all():
        if line.discard_movement_id:
            reverse_movement(line.discard_movement, user, reason, occurred_at)
        if line.return_movement_id:
            reverse_movement(line.return_movement, user, reason, occurred_at)
        if line.lifecycle_event_id:
            SpecificPlantLocation.objects.filter(
                specific_plant=line.fulfillment_line.allocation.plant,
                ended__isnull=True,
            ).update(ended=occurred_at)
            reverse_lifecycle_event(line.lifecycle_event, user, reason, occurred_at)
        SalesOrderAllocation.objects.filter(
            pk=line.fulfillment_line.allocation_id,
        ).update(status=SalesOrderAllocation.Status.FULFILLED, updated=timezone.now())
    # Closing the case comes last on purpose. A release records the fact that a
    # quarantined plant recovered, and this reversal says the return that
    # quarantined it never happened at all. Reversing the return facts first
    # leaves every plant back at sold, so the release closes the case without
    # claiming a recovery from a quarantine nothing now records.
    if original.quarantine_case_id:
        act_on_quarantine(
            original.workspace, user, original.quarantine_case,
            action_name='release', idempotency_key=uuid5(operation_key, 'release'),
            reason=reason, occurred_at=occurred_at,
        )
    recompute_order_status(original.order)
    return reversal
