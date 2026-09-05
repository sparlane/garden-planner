"""Atomic fulfillment, payment, return, refund, and reversal commands."""

# The services coordinate several ledgers deliberately in one transaction.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
# pylint: disable=too-many-arguments
# One posting command per commercial event, each one whole in a single
# transaction. Splitting the file would separate a command from the reversal
# that has to undo exactly what it did.
# pylint: disable=too-many-lines

import hashlib
import json
from decimal import Decimal
from uuid import uuid5

from django.core.exceptions import ValidationError
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
from plantings.cohort_availability import DISPATCHABLE_STATES
from plantings.cohorts import lock_cohorts
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from plantings.models import PlantCohort, SpecificPlant, SpecificPlantLocation

from .calculations import line_position_amounts, money, proportional_refund
from .cohort_stock import (
    dispatch_cohort_stock,
    recost_cohort_batches,
    restore_cohort_stock,
    return_cohort_stock,
    withdraw_returned_cohort,
)
from .containers import (
    recost_container_plants,
    resolve_riders,
    return_event,
    return_riders,
    riders_of,
    sell_rider,
    validate_riders_are_free,
)
from .models import (
    Fulfillment,
    FulfillmentLine,
    FulfillmentNumberSequence,
    FulfillmentPackagingLine,
    Payment,
    Refund,
    RefundLine,
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderShortfall,
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

    Deriving it from the allocation rather than snapshotting a column on
    `FulfillmentLine` keeps the promise and the dispatch the same figure —
    which is why this is `promised_units` read from the dispatch side rather
    than a second count of its own.
    """
    return allocation.promised_units


def shortfall_quantity(order):
    """Return how much of this order's commitment was given up unsupplied."""
    return SalesOrderShortfall.objects.filter(line__order=order).aggregate(
        total=Sum('quantity'),
    )['total'] or 0


def outstanding_quantity(order):
    """Return what this order still owes: ordered, less short, less shipped.

    Both subtractions are the same idea from two sides. A dispatch supplies a
    unit and a shortfall says one will never be supplied, and an order with
    neither left outstanding is finished whichever way its units went.
    """
    returned = _effective_return_line_ids(order)
    fulfilled = sum(
        dispatched_quantity(row.allocation)
        for row in _effective_fulfillment_lines(order).exclude(pk__in=returned)
    )
    requested = sum(order.lines.values_list('quantity', flat=True))
    return requested - shortfall_quantity(order) - fulfilled, fulfilled


def recompute_order_status(order):
    """Derive fulfillment status from effective dispatch, return, and shortfall."""
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    if order.status == SalesOrder.Status.CANCELLED:
        return order
    outstanding, fulfilled = outstanding_quantity(order)
    if outstanding <= 0:
        # Nothing is owed, whether because it all shipped or because what did
        # not ship was written off as short. An order that supplied none of
        # what it promised never reaches here: `record_shortfall` refuses the
        # last of it and says to cancel the order instead, because "fulfilled"
        # would be a plainly false word for a load that never left.
        next_status = SalesOrder.Status.FULFILLED
    elif fulfilled == 0:
        next_status = SalesOrder.Status.CONFIRMED
    else:
        next_status = SalesOrder.Status.PARTIALLY_FULFILLED
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


def _require_ready_cohorts(allocations, cohorts):
    """Refuse a dispatch of anonymous stock nobody has graded ready yet.

    Committing and shipping are separate questions: an order may promise stock
    that is still in plugs, and `plantings.cohort_availability` says so, but the
    plants have to actually exist on a trolley before they leave. The check runs
    over the whole dispatch before any of it is written, so an operator picking
    six lines is told every block that is not ready rather than one per attempt
    — `plantings.cohorts.sell_cohort` would refuse the first and say nothing
    about the rest.
    """
    promised = [
        cohorts[allocation.plant_cohort_id]
        for allocation in allocations
        if allocation.plant_cohort_id
    ]
    waiting = sorted(
        cohort.pk for cohort in promised
        if cohort.lifecycle_state not in DISPATCHABLE_STATES
    )
    if not waiting:
        return
    listed = ', '.join(str(cohort_id) for cohort_id in waiting)
    raise ValidationError({
        'allocations': (
            f'Not ready to dispatch: cohort {listed}. Grade the stock ready, '
            f'or record a shortfall against the order.'
        ),
    })


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
        'plant_cohort__batch', 'source_location',
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
    cohorts = lock_cohorts(
        order.workspace,
        [row.plant_cohort_id for row in allocations if row.plant_cohort_id],
    )
    _require_ready_cohorts(allocations, cohorts)
    riders = resolve_riders(units, set(plant_ids))
    validate_riders_are_free(riders, order)
    positions = _available_positions(order)
    passengers = []
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
        cohort_event = None
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
        elif allocation.plant_cohort_id:
            cohort_event, cogs_amount, provisional = dispatch_cohort_stock(
                order, user, allocation, cohorts[allocation.plant_cohort_id],
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
            cohort_event=cohort_event,
            **amounts,
        )
        for placement, (rider_cost, _flag) in zip(carried, rider_costs):
            sell_rider(line, placement, user, fulfilled_at, rider_cost)
            passengers.append(placement.specific_plant)
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
    recost_container_plants(passengers, user, 'Sold inside its container.')
    recost_cohort_batches(cohorts.values(), user, 'Anonymous stock dispatched.')
    recompute_order_status(order)
    return fulfillment


@transaction.atomic
def record_shortfall(order, user, *, allocation_id, quantity, reason, recorded_at=None):
    """Give up the part of a commitment the stock cannot meet, keeping the rest.

    This is the outcome a forward order needs and a dispatch cannot give it. By
    the time a load is being picked, "these plants never grew" is not a bad
    request to be refused — it is the answer, and it belongs on the order where
    the customer's claim, the season review and the production plan can all read
    it. Refusing it at dispatch instead leaves an operator editing the promise
    away, and with it the record that the promise was ever made.

    Allocations are immutable, so shrinking one is not possible: the whole
    promise is closed short and the remainder re-promised against the same
    pool in the same transaction, which is what stops the kept part falling
    back into availability for somebody else to take in between. Only a counted
    draw ever has a remainder — an identity promises exactly one thing, and one
    thing fails whole — so the replacement copies the pool columns and nothing
    else.

    An order that supplied nothing at all is refused: writing off the last of a
    commitment would leave a "fulfilled" order that never shipped, and what the
    operator means in that case is a cancellation.
    """
    reason = reason.strip()
    if not reason:
        raise ValidationError({'reason': 'A shortfall requires a stated reason.'})
    recorded_at = recorded_at or timezone.now()
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    if order.status not in {
            SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_FULFILLED}:
        raise ValidationError({
            'status': 'Only a confirmed incomplete order can be short-supplied.',
        })
    allocation = SalesOrderAllocation.objects.select_for_update(of=('self',)).select_related(
        'line',
    ).filter(
        pk=allocation_id,
        line__order=order,
        status=SalesOrderAllocation.Status.RESERVED,
    ).first()
    if allocation is None:
        raise ValidationError({
            'allocation': 'Select an active reservation on this order.',
        })
    promised = allocation.promised_units
    if not 1 <= quantity <= promised:
        raise ValidationError({
            'quantity': f'A shortfall covers between 1 and {promised} of this promise.',
        })
    outstanding, fulfilled = outstanding_quantity(order)
    if fulfilled == 0 and outstanding - quantity <= 0:
        raise ValidationError({
            'quantity': 'This order would have nothing left to supply; cancel it instead.',
        })
    SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
        status=SalesOrderAllocation.Status.SHORTFALL, updated=timezone.now(),
    )
    allocation.status = SalesOrderAllocation.Status.SHORTFALL
    ReservationEvent.objects.create(
        allocation=allocation, event_type=ReservationEvent.EventType.SHORTFALL,
        occurred_at=recorded_at, reason=reason, created_by=_actor(user),
    )
    replacement = None
    if promised > quantity:
        replacement = SalesOrderAllocation.objects.create(
            line=allocation.line,
            plant_cohort=allocation.plant_cohort,
            stock_lot=allocation.stock_lot,
            source_location=allocation.source_location,
            quantity=promised - quantity,
            status=SalesOrderAllocation.Status.RESERVED,
            expires_at=allocation.expires_at,
            created_by=_actor(user),
        )
        ReservationEvent.objects.create(
            allocation=replacement, event_type=ReservationEvent.EventType.RESERVED,
            occurred_at=recorded_at, created_by=_actor(user),
        )
    shortfall = SalesOrderShortfall.objects.create(
        line=allocation.line, allocation=allocation, replacement=replacement,
        quantity=quantity, reason=reason, recorded_at=recorded_at,
        created_by=_actor(user),
    )
    recompute_order_status(order)
    return shortfall


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
                        'allocation__stock_lot__item', 'allocation__source_location',
                        'allocation__plant_cohort__batch')
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
    lock_cohorts(order.workspace, [
        row.allocation.plant_cohort_id for row in lines.values()
        if row.allocation.plant_cohort_id
    ])
    already = _effective(SalesReturn.objects.filter(order=order)).filter(
        lines__fulfillment_line_id__in=line_ids,
    ).exists()
    if already:
        raise ValidationError({'items': 'One or more items were already returned.'})
    quarantined_plants = []
    quarantined_cohorts = []
    returned_cohorts = []
    returned_riders = []
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
        cohort_event = None
        if allocation.plant_cohort_id:
            cohort_event = return_cohort_stock(
                order, user, line, sales_return,
                returned_at=returned_at, reason=reason, outcome=outcome,
                destination=destination,
            )
            returned_cohorts.append(cohort_event.cohort)
            if outcome == SalesReturnLine.Outcome.QUARANTINED:
                quarantined_cohorts.append(cohort_event.cohort)
        elif allocation.stock_lot_id:
            return_movement, discard_movement = _return_counted_stock(
                order, user, line, sales_return,
                returned_at=returned_at, reason=reason, outcome=outcome,
                destination=destination,
            )
        elif allocation.plant_id:
            lifecycle_event = record_lifecycle_event(
                allocation.plant, user,
                OutcomeRequest(
                    return_event(outcome), occurred_at=returned_at, reason=reason,
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
            returned_riders.extend(
                rider.plant for rider in line.riders.select_related('plant')
            )
            quarantined_plants.extend(
                return_riders(line, sales_return, user, outcome),
            )
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
            cohort_event=cohort_event,
            return_movement=return_movement, discard_movement=discard_movement,
        )
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
            status=SalesOrderAllocation.Status.RETURNED, updated=timezone.now(),
        )
    if quarantined_plants or quarantined_cohorts:
        if observation_type is None or severity is None:
            raise ValidationError({'health': 'Quarantined plants need an observation type and severity.'})
        scopes = [{'type': 'plant', 'id': plant.pk} for plant in quarantined_plants]
        scopes += [{'type': 'cohort', 'id': cohort.pk} for cohort in quarantined_cohorts]
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
    recost_container_plants(returned_riders, user, 'Returned in its container.')
    recost_cohort_batches(returned_cohorts, user, 'Anonymous stock returned.')
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
    fulfilled = list(_effective_fulfillment_lines(order))
    returned_ids = _effective_return_line_ids(order)
    refunds = _effective(order.refunds.all())
    payments = _effective(order.payments.all())
    fulfilled_total = sum((row.total_incl_tax for row in fulfilled), Decimal('0'))
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
        # All four are counted in units, so they can be read against each
        # other: one counted allocation can promise fifty pots, and reporting
        # it as a single reservation beside a requested fifty would say the
        # order was barely started when it is completely covered.
        'reserved_quantity': sum(
            dispatched_quantity(row) for row in SalesOrderAllocation.objects.filter(
                line__order=order, status=SalesOrderAllocation.Status.RESERVED,
            )
        ),
        # The part of the reservation that is a promise about the future: stock
        # this order holds in a block nobody has graded ready. Reported beside
        # the reservation rather than folded into it, because a salesperson
        # answering "when can you deliver?" needs the two figures apart.
        'committed_forward_quantity': sum(
            dispatched_quantity(row) for row in SalesOrderAllocation.objects.filter(
                line__order=order,
                status=SalesOrderAllocation.Status.RESERVED,
                plant_cohort__lifecycle_state=PlantCohort.LifecycleState.GROWING,
            )
        ),
        'short_quantity': shortfall_quantity(order),
        'fulfilled_quantity': sum(
            dispatched_quantity(row.allocation) for row in fulfilled
        ),
        'returned_quantity': sum(
            dispatched_quantity(row.allocation)
            for row in fulfilled if row.pk in returned_ids
        ),
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
    lock_cohorts(original.workspace, [
        line.allocation.plant_cohort_id for line in original.lines.all()
        if line.allocation.plant_cohort_id
    ])
    reversal = Fulfillment.objects.create(
        workspace=original.workspace, order=original.order,
        fulfillment_number=_number(original.workspace), fulfilled_at=occurred_at,
        notes=reason.strip(), reversal_of=original, operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )
    restored = []
    for line in original.lines.all():
        if line.lifecycle_event_id:
            reverse_lifecycle_event(line.lifecycle_event, user, reason, occurred_at)
        if line.stock_movement_id:
            reverse_movement(line.stock_movement, user, reason, occurred_at)
        if line.cohort_event_id:
            restored.append(restore_cohort_stock(
                user, line, reversal, occurred_at=occurred_at, reason=reason,
            ))
        SalesOrderAllocation.objects.filter(pk=line.allocation_id).update(
            status=SalesOrderAllocation.Status.RESERVED, updated=timezone.now(),
        )
    for packaging in original.packaging_lines.all():
        reverse_movement(packaging.stock_movement, user, reason, occurred_at)
    recost_container_plants(
        [rider.plant for rider in riders_of(original)], user, reason,
    )
    recost_cohort_batches(restored, user, reason)
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
    lock_cohorts(original.workspace, [
        line.fulfillment_line.allocation.plant_cohort_id
        for line in original.lines.all()
        if line.fulfillment_line.allocation.plant_cohort_id
    ])
    targets = [
        line.fulfillment_line.allocation.pk for line in original.lines.all()
        if _returned_stock_moved_on(line)
    ]
    if targets:
        raise ValidationError({'sales_return': 'Returned stock has already been reallocated.'})
    reversal = SalesReturn.objects.create(
        workspace=original.workspace, order=original.order,
        returned_at=occurred_at, reason=reason.strip(), reversal_of=original,
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )
    withdrawn = []
    for line in original.lines.all():
        if line.discard_movement_id:
            reverse_movement(line.discard_movement, user, reason, occurred_at)
        if line.return_movement_id:
            reverse_movement(line.return_movement, user, reason, occurred_at)
        if line.cohort_event_id:
            withdrawn.append(withdraw_returned_cohort(
                user, line, reversal, occurred_at=occurred_at, reason=reason,
            ))
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
    recost_container_plants([
        rider.plant
        for line in original.lines.all()
        for rider in line.fulfillment_line.riders.select_related('plant')
    ], user, reason)
    recost_cohort_batches(withdrawn, user, reason)
    recompute_order_status(original.order)
    return reversal


def _returned_stock_moved_on(line):
    """Return whether one returned promise's stock has been claimed since.

    An identity can be in only one place, so another live promise naming it is
    proof the return cannot be undone. A lot has no identity to claim: taking
    the quantity back out again is measured by the ledger, which refuses to
    drive a lot negative, so there is nothing useful to say here. A returned
    cohort is a block of its own, and what has to hold is that it still holds
    what came back — a split, a promotion, a write-off or a second sale out of
    it all leave nothing to take away again.
    """
    allocation = line.fulfillment_line.allocation
    kind = allocation.target_kind
    if kind in ('plant', 'inventory_unit'):
        return SalesOrderAllocation.objects.filter(
            **{f'{kind}_id': getattr(allocation, f'{kind}_id')},
            status__in=[SalesOrderAllocation.Status.RESERVED,
                        SalesOrderAllocation.Status.FULFILLED],
        ).exclude(pk=allocation.pk).exists()
    if kind == 'plant_cohort':
        returned = line.cohort_event
        return returned is None or returned.cohort.quantity < returned.quantity_delta
    return False
