"""Transactional commands for sales orders and exact reservations."""

# pylint: disable=too-many-locals

from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from costing.services import plant_cost_breakdown
from health.availability import is_quarantined
from inventory.ledger import lock_lots, lock_units, unit_physical_state, unpromised_bulk
from inventory.models import InventoryUnit, StockLot
from locations.models import Location
from plantings.lifecycle import SELLABLE_STATES, plant_lifecycle_summary
from plantings.models import SpecificPlant

from .calculations import money
from .models import (
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesOrderNumberSequence,
)


TENTATIVE_CLAIM = 'tentatively_claimed'

#: The order statuses that may still take on new promises of stock.
ALLOCATABLE_ORDER_STATUSES = frozenset({
    SalesOrder.Status.QUOTE,
    SalesOrder.Status.DRAFT,
    SalesOrder.Status.CONFIRMED,
    SalesOrder.Status.PARTIALLY_FULFILLED,
})

#: The statuses that hold stock away from anybody else. A pending selection is
#: tentative by design and warns rather than blocks, exactly as it does for a
#: plant somebody else has put in a draft.
HOLDING_STATUSES = (SalesOrderAllocation.Status.RESERVED,)


class LotRequest(NamedTuple):
    """One counted draw on one lot standing at one place.

    Identifiers rather than instances, because the caller has ids and a
    request naming a lot that does not exist has to be reportable as a
    conflict rather than raised as a lookup failure.
    """

    lot: int
    location: int
    quantity: int


@transaction.atomic
def create_order(workspace, user, **values):
    """Create an order with locked numbering and workspace term snapshots."""
    sequence, _created = SalesOrderNumberSequence.objects.select_for_update().get_or_create(
        workspace=workspace,
    )
    number = sequence.next_number
    sequence.next_number += 1
    sequence.save(update_fields=['next_number'])
    status = values.pop('status', SalesOrder.Status.DRAFT)
    today = timezone.localdate()
    if status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
        raise ValidationError({'status': 'Orders must begin as a quote or draft.'})
    defaults = {
        'currency_code': workspace.currency_code,
        'prices_include_tax': workspace.sales_prices_include_tax,
        'created_by': user,
        'quote_date': today if status == SalesOrder.Status.QUOTE else None,
        'order_date': today if status == SalesOrder.Status.DRAFT else None,
    }
    defaults.update(values)
    return SalesOrder.objects.create(
        workspace=workspace,
        order_number=f'SO-{number:06d}',
        status=status,
        **defaults,
    )


@transaction.atomic
def update_pricing_mode(order, prices_include_tax):
    """Reinterpret entered draft terms in a newly selected pricing mode."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
        raise ValidationError({'status': 'Confirmed commercial terms are immutable.'})
    order.prices_include_tax = prices_include_tax
    order.save(update_fields=['prices_include_tax', 'updated'])
    for line in order.lines.select_related('order').order_by('pk'):
        line.order = order
        line.save()
    order.refresh_from_db()
    return order


@transaction.atomic
def quote_to_draft(order):
    """Accept a quote into an editable order without changing its terms."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != SalesOrder.Status.QUOTE:
        raise ValidationError({'status': 'Only a quote can become a draft.'})
    SalesOrder.objects.filter(pk=order.pk).update(
        status=SalesOrder.Status.DRAFT,
        order_date=timezone.localdate(),
        updated=timezone.now(),
    )
    order.refresh_from_db()
    return order


def _locked_plants(workspace, plant_ids):
    """Lock concrete plants in deterministic identifier order."""
    requested = sorted(set(plant_ids))
    plants = list(
        SpecificPlant.objects.select_for_update(of=('self',))
        .select_related('batch__variety')
        .filter(workspace=workspace, pk__in=requested)
        .order_by('pk')
    )
    if len(plants) != len(requested):
        raise ValidationError({'plants': 'One or more plants are unavailable.'})
    return {plant.pk: plant for plant in plants}


def _held_elsewhere(line, **identity):
    """Return whether another line already holds this exact identity."""
    return SalesOrderAllocation.objects.filter(
        status__in=HOLDING_STATUSES, **identity,
    ).exclude(line=line).exists()


def _plant_target_error(line, target):
    """Return why a locked plant cannot currently satisfy a seedling line."""
    if target.batch.variety_id != line.variety_id:
        return 'wrong_variety'
    if plant_lifecycle_summary(target).state not in SELLABLE_STATES:
        return 'not_sellable'
    if is_quarantined(target):
        return 'quarantined'
    if _held_elsewhere(line, plant=target):
        return 'already_reserved'
    return None


def _unit_target_error(line, target):
    """Return why a locked numbered unit cannot currently satisfy a unit line."""
    if target.item_id != line.item_id:
        return 'wrong_item'
    if unit_physical_state(target) != 'available':
        return 'not_available'
    if _held_elsewhere(line, inventory_unit=target):
        return 'already_reserved'
    return None


#: One resolver per identity line type, and the column each one competes over.
#: A counted line is absent on purpose: it promises no identity, so there is
#: nothing here for it to be looked up by.
IDENTITY_TARGETS = {
    SalesOrderLine.LineType.SEEDLING: (_plant_target_error, 'plant'),
    SalesOrderLine.LineType.UNIT: (_unit_target_error, 'inventory_unit'),
}


def _target_error(line, target):
    """Return why a locked identity cannot currently satisfy this line."""
    return IDENTITY_TARGETS[line.line_type][0](line, target)


def _target_allocations(line, target, statuses):
    """Return other active claims for a target, with readable order context."""
    identity = {IDENTITY_TARGETS[line.line_type][1]: target}
    return (
        SalesOrderAllocation.objects
        .filter(**identity, status__in=statuses)
        .exclude(line=line)
        .select_related('line__order')
        .order_by('line__order__order_number', 'pk')
    )


def _lot_request_error(line, lot, location, request, taken):
    """Return why a counted draw cannot be met, and what is actually free.

    `taken` is what earlier requests in the same basket have already claimed,
    so two draws on one lot cannot each be told the whole pool is theirs.
    """
    if lot is None:
        return 'unknown', None
    if lot.workspace_id != line.order.workspace_id:
        return 'wrong_workspace', None
    if lot.item_id != line.item_id:
        return 'wrong_item', None
    if location is None or location.workspace_id != line.order.workspace_id:
        return 'unknown_location', None
    available = unpromised_bulk(lot, location) - taken
    if Decimal(request.quantity) > available:
        return 'insufficient_stock', available
    return None, available


def _allocation_reference(allocation):
    """Describe the competing promise without exposing mutable line details."""
    return {
        'order': allocation.line.order_id,
        'order_number': allocation.line.order.order_number,
        'status': allocation.status,
    }


def _reject_foreign_selection(line, plant_ids, unit_ids, lot_requests):
    """Refuse a selection of a kind this line cannot promise, before locking."""
    offered = {'plants': plant_ids, 'units': unit_ids, 'lots': lot_requests}
    accepted = {
        SalesOrderLine.LineType.SEEDLING: ('plants', 'A seedling line accepts plants only.'),
        SalesOrderLine.LineType.UNIT: ('units', 'A unit line accepts numbered units only.'),
        SalesOrderLine.LineType.LOT_QUANTITY: ('lots', 'A counted line accepts lot quantities only.'),
    }[line.line_type]
    for field, values in offered.items():
        if field != accepted[0] and values:
            raise ValidationError({field: accepted[1]})


def preview_targets(line, plant_ids=(), unit_ids=(), lot_requests=()):
    """Resolve an explicit selection to what can be had and what cannot."""
    _reject_foreign_selection(line, plant_ids, unit_ids, lot_requests)
    if line.line_type == SalesOrderLine.LineType.LOT_QUANTITY:
        return _preview_lot_requests(line, lot_requests)
    return _preview_identities(line, plant_ids, unit_ids)


def _preview_lot_requests(line, lot_requests):
    """Resolve counted draws against the availability arithmetic behind them.

    Requests are answered in order and each one's accepted quantity is held
    against the pool for the ones after it, so a basket asking twice for the
    same lot is told the truth the second time too.
    """
    requests = [LotRequest(*row) for row in lot_requests]
    lots = {
        row.pk: row for row in StockLot.objects.filter(
            pk__in={request.lot for request in requests},
        ).select_related('item')
    }
    locations = {
        row.pk: row for row in Location.objects.filter(
            pk__in={request.location for request in requests},
        )
    }
    taken = {}
    selected = []
    conflicts = []
    for request in requests:
        lot = lots.get(request.lot)
        location = locations.get(request.location)
        key = (request.lot, request.location)
        reason, available = _lot_request_error(
            line, lot, location, request, taken.get(key, Decimal('0')),
        )
        row = {
            'id': request.lot,
            'location': request.location,
            'quantity': request.quantity,
            # Fixed at the quantity column's own precision, because a bare
            # `:f` renders whatever precision the backend's aggregate happened
            # to return — '500' on SQLite and '500.000000000' on PostgreSQL for
            # the same stock. `formatQuantity` trims the padding losslessly.
            'available': None if available is None else f'{available:.9f}',
        }
        if reason:
            conflicts.append({**row, 'reason': reason})
            continue
        taken[key] = taken.get(key, Decimal('0')) + Decimal(request.quantity)
        selected.append(row)
    return {'selected': selected, 'conflicts': conflicts, 'warnings': []}


def _preview_identities(line, plant_ids, unit_ids):
    """Resolve explicit target IDs to compatible selections and conflicts."""
    workspace = line.order.workspace
    model = SpecificPlant if plant_ids else InventoryUnit
    ids = sorted(set(plant_ids or unit_ids))
    targets = {
        row.pk: row
        for row in model.objects.filter(pk__in=ids).select_related(
            'batch__variety' if model is SpecificPlant else 'item',
        )
    }
    selected = []
    conflicts = []
    warnings = []
    for target_id in ids:
        target = targets.get(target_id)
        if target is None:
            conflicts.append({'id': target_id, 'reason': 'unknown'})
            continue
        if target.workspace_id != workspace.pk:
            conflicts.append({'id': target_id, 'reason': 'wrong_workspace'})
            continue
        reason = _target_error(line, target)
        if reason:
            conflict = {'id': target_id, 'reason': reason}
            if reason == 'already_reserved':
                holder = _target_allocations(
                    line, target, [SalesOrderAllocation.Status.RESERVED],
                ).first()
                if holder is not None:
                    conflict.update(_allocation_reference(holder))
            conflicts.append(conflict)
        else:
            selected.append(target_id)
            for claim in _target_allocations(
                    line, target, [SalesOrderAllocation.Status.PENDING]):
                warnings.append({
                    'id': target_id,
                    'reason': TENTATIVE_CLAIM,
                    **_allocation_reference(claim),
                })
    return {'selected': selected, 'conflicts': conflicts, 'warnings': warnings}


def _promised_quantity(line):
    """Total what this line's live allocations already promise.

    An identity allocation is worth exactly one, which is why `quantity` is
    null on it rather than stored as a one nothing may contradict.
    """
    total = Decimal('0')
    active = line.allocations.filter(
        status__in=[SalesOrderAllocation.Status.PENDING, SalesOrderAllocation.Status.RESERVED],
    ).values_list('quantity', flat=True)
    for quantity in active:
        total += Decimal(quantity if quantity is not None else 1)
    return total


def _next_status(order):
    """Return whether a new promise on this order reserves stock at once."""
    immediate = order.status in {
        SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_FULFILLED,
    }
    return (
        SalesOrderAllocation.Status.RESERVED if immediate
        else SalesOrderAllocation.Status.PENDING
    ), immediate


# One parameter per kind of target a line can promise. Collapsing them into
# one bag would make every caller say which kind it meant anyway, and lose the
# refusal that catches a plant offered to a tray line before any lock is taken.
@transaction.atomic
def allocate_targets(line, user, plant_ids=(), unit_ids=(), lot_requests=(),  # pylint: disable=too-many-arguments,too-many-positional-arguments
                     expires_at=None):
    """Add tentative targets, or immediately reserve confirmed replacements."""
    order = SalesOrder.objects.select_for_update().get(pk=line.order_id)
    line = SalesOrderLine.objects.select_related('order').get(pk=line.pk, order=order)
    if order.status not in ALLOCATABLE_ORDER_STATUSES:
        raise ValidationError({'status': 'This order cannot accept allocations.'})
    _reject_foreign_selection(line, plant_ids, unit_ids, lot_requests)
    if line.line_type == SalesOrderLine.LineType.LOT_QUANTITY:
        return _allocate_lot_requests(line, order, user, lot_requests, expires_at)
    return _allocate_identities(
        line, order, user, sorted(set(plant_ids or unit_ids)), expires_at,
    )


def _allocate_identities(line, order, user, ids, expires_at):
    """Attach exact plants or numbered units, each of them worth one."""
    targets = (
        _locked_plants(order.workspace, ids)
        if line.line_type == SalesOrderLine.LineType.SEEDLING
        else lock_units(order.workspace, ids)
    )
    if _promised_quantity(line) + len(ids) > line.quantity:
        raise ValidationError({'allocations': 'Allocations cannot exceed the requested quantity.'})
    status, immediate = _next_status(order)
    column = IDENTITY_TARGETS[line.line_type][1]
    created = []
    try:
        for target_id in ids:
            target = targets[target_id]
            reason = _target_error(line, target)
            if reason:
                raise ValidationError({'allocations': f'Target {target_id}: {reason}.'})
            allocation = SalesOrderAllocation.objects.create(
                line=line,
                status=status,
                expires_at=expires_at,
                created_by=user,
                **{column: target},
            )
            if immediate:
                _event(allocation, ReservationEvent.EventType.RESERVED, user)
            created.append(allocation)
    except IntegrityError as exc:
        raise ValidationError({'allocations': 'One or more targets were reserved concurrently.'}) from exc
    return created


def _allocate_lot_requests(line, order, user, lot_requests, expires_at):
    """Attach counted draws on anonymous stock, holding the lot lock throughout.

    The lock is taken before availability is read and kept for the whole
    transaction, which is what makes this serialise against another order's
    reservation and against `inventory.ledger.individualize_lot_units` drawing
    on the very same pots.
    """
    requests = [LotRequest(*row) for row in lot_requests]
    if not requests:
        raise ValidationError({'lots': 'Select at least one quantity to draw.'})
    lots = lock_lots(order.workspace, [request.lot for request in requests])
    locations = {
        row.pk: row for row in Location.objects.filter(
            workspace=order.workspace,
            pk__in={request.location for request in requests},
        )
    }
    requested = sum(Decimal(request.quantity) for request in requests)
    if _promised_quantity(line) + requested > line.quantity:
        raise ValidationError({'allocations': 'Allocations cannot exceed the requested quantity.'})
    status, immediate = _next_status(order)
    taken = {}
    created = []
    for request in requests:
        location = locations.get(request.location)
        key = (request.lot, request.location)
        reason, _available = _lot_request_error(
            line, lots[request.lot], location, request, taken.get(key, Decimal('0')),
        )
        if reason:
            raise ValidationError({'allocations': f'Lot {request.lot}: {reason}.'})
        taken[key] = taken.get(key, Decimal('0')) + Decimal(request.quantity)
        allocation = SalesOrderAllocation.objects.create(
            line=line,
            stock_lot=lots[request.lot],
            source_location=location,
            quantity=request.quantity,
            status=status,
            expires_at=expires_at,
            created_by=user,
        )
        if immediate:
            _event(allocation, ReservationEvent.EventType.RESERVED, user)
        created.append(allocation)
    return created


@transaction.atomic
def deallocate_pending(order, allocation_ids):
    """Remove tentative selections before they ever become reservations."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
        raise ValidationError({'status': 'Only tentative allocations can be removed.'})
    allocations = list(
        SalesOrderAllocation.objects.filter(
            pk__in=allocation_ids,
            line__order=order,
            status=SalesOrderAllocation.Status.PENDING,
        ).order_by('pk')
    )
    if len(allocations) != len(set(allocation_ids)):
        raise ValidationError({'allocations': 'One or more tentative allocations are unavailable.'})
    for allocation in allocations:
        allocation.delete()


def _event(allocation, event_type, user, reason=''):
    """Append one reservation fact at the service action's current time."""
    return ReservationEvent.objects.create(
        allocation=allocation,
        event_type=event_type,
        occurred_at=timezone.now(),
        reason=reason.strip(),
        created_by=user,
    )


def _validate_pending_identity(allocation, plants, units):
    """Refuse to reserve an identity somebody else took while we drafted."""
    target = (
        plants[allocation.plant_id] if allocation.plant_id
        else units[allocation.inventory_unit_id]
    )
    reason = _target_error(allocation.line, target)
    if reason is None:
        return
    holder = _target_allocations(
        allocation.line, target, [SalesOrderAllocation.Status.RESERVED],
    ).first()
    held_by = f' by {holder.line.order.order_number}' if holder else ''
    raise ValidationError({'allocations': f'Target {target.pk}: {reason}{held_by}.'})


def _validate_pending_draws(pending, lots):
    """Refuse to reserve more anonymous stock than is standing unpromised.

    The whole order's draws on one lot and place are added together first, so
    two counted lines cannot each be told the same pots are theirs. The lots
    are already locked by the caller, which is what makes the figure hold.
    """
    wanted = {}
    for allocation in pending:
        if allocation.stock_lot_id is None:
            continue
        key = (allocation.stock_lot_id, allocation.source_location_id)
        wanted[key] = wanted.get(key, Decimal('0')) + Decimal(allocation.quantity)
    for (lot_id, location_id), quantity in sorted(wanted.items()):
        location = Location.objects.get(pk=location_id)
        available = unpromised_bulk(lots[lot_id], location)
        if quantity > available:
            raise ValidationError({
                'allocations': (
                    f'Lot {lot_id}: only {available:.9f} is unpromised at '
                    f'{location.name}.'
                ),
            })


@transaction.atomic
def confirm_order(order, user):
    """Atomically validate and reserve every exact unit promised by a draft."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != SalesOrder.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft order can be confirmed.'})
    lines = list(order.lines.prefetch_related('allocations').order_by('pk'))
    if not lines:
        raise ValidationError({'lines': 'Add at least one order line.'})
    pending = [allocation for line in lines for allocation in line.allocations.all() if allocation.status == SalesOrderAllocation.Status.PENDING]
    for line in lines:
        if _promised_quantity(line) != line.quantity:
            raise ValidationError({'lines': f'Line {line.pk} requires exactly {line.quantity} allocations.'})
    plant_ids = [allocation.plant_id for allocation in pending if allocation.plant_id]
    unit_ids = [allocation.inventory_unit_id for allocation in pending if allocation.inventory_unit_id]
    lot_ids = [allocation.stock_lot_id for allocation in pending if allocation.stock_lot_id]
    plants = _locked_plants(order.workspace, plant_ids)
    units = lock_units(order.workspace, unit_ids)
    lots = lock_lots(order.workspace, lot_ids)
    _validate_pending_draws(pending, lots)
    try:
        for allocation in sorted(pending, key=lambda row: row.pk):
            if allocation.stock_lot_id is None:
                _validate_pending_identity(allocation, plants, units)
            SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
                status=SalesOrderAllocation.Status.RESERVED,
                updated=timezone.now(),
            )
            allocation.status = SalesOrderAllocation.Status.RESERVED
            _event(allocation, ReservationEvent.EventType.RESERVED, user)
    except IntegrityError as exc:
        raise ValidationError({'allocations': 'One or more targets were reserved concurrently.'}) from exc
    SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED, updated=timezone.now())
    order.refresh_from_db()
    return order


@transaction.atomic
def close_reservations(order, user, allocation_ids, action, reason=''):
    """Release or explicitly expire selected unfulfilled reservations."""
    statuses = {
        'release': (SalesOrderAllocation.Status.RELEASED, ReservationEvent.EventType.RELEASED),
        'expire': (SalesOrderAllocation.Status.EXPIRED, ReservationEvent.EventType.EXPIRED),
        'cancel': (SalesOrderAllocation.Status.RELEASED, ReservationEvent.EventType.CANCELLED),
    }
    if action not in statuses:
        raise ValueError('Unknown reservation closing action.')
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    allocations = list(
        SalesOrderAllocation.objects.select_for_update()
        .filter(line__order=order, status=SalesOrderAllocation.Status.RESERVED, pk__in=allocation_ids)
        .order_by('pk')
    )
    if len(allocations) != len(set(allocation_ids)):
        raise ValidationError({'allocations': 'One or more active reservations are unavailable.'})
    if action == 'expire':
        not_due = [
            allocation.pk for allocation in allocations
            if allocation.expires_at is None or allocation.expires_at > timezone.now()
        ]
        if not_due:
            raise ValidationError({'allocations': f'Reservations are not expired: {not_due}.'})
    next_status, event_type = statuses[action]
    for allocation in allocations:
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(status=next_status, updated=timezone.now())
        allocation.status = next_status
        _event(allocation, event_type, user, reason)
    return allocations


@transaction.atomic
def cancel_order(order, user, reason=''):
    """Cancel an incomplete order and release every unfulfilled reservation."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == SalesOrder.Status.FULFILLED:
        raise ValidationError({'status': 'A fulfilled order must use the return/refund workflow.'})
    if order.status == SalesOrder.Status.CANCELLED:
        raise ValidationError({'status': 'This order is already cancelled.'})
    reserved_ids = list(
        order.lines.filter(
            allocations__status=SalesOrderAllocation.Status.RESERVED,
        ).values_list('allocations__pk', flat=True)
    )
    if reserved_ids:
        close_reservations(order, user, reserved_ids, 'cancel', reason)
    SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CANCELLED, updated=timezone.now())
    order.refresh_from_db()
    return order


def _allocated_cost(allocation):
    """Return one promise's cost, whether it is known, and whether it is final.

    A counted draw is valued from its own lot's unit cost, because that is the
    price the pots in that box were bought at; a second delivery of the same
    item is a different lot and cost something else.
    """
    if allocation.plant_id:
        breakdown = plant_cost_breakdown(allocation.plant)
        value = breakdown['provisional_value'] or breakdown['final_value']
        return value, breakdown['unknown_cost'], breakdown['provisional']
    if allocation.stock_lot_id:
        unit_cost = allocation.stock_lot.base_unit_cost
        if unit_cost is None:
            return None, True, False
        return money(Decimal(allocation.quantity) * unit_cost), False, False
    value = allocation.inventory_unit.acquisition_cost
    return value, value is None, False


def order_margin(order):
    """Return an ex-tax margin only when every allocated cost is known."""
    allocations = list(
        SalesOrderAllocation.objects.filter(
            line__order=order,
            status__in=[SalesOrderAllocation.Status.PENDING, SalesOrderAllocation.Status.RESERVED, SalesOrderAllocation.Status.FULFILLED],
        ).select_related('plant__batch', 'inventory_unit', 'stock_lot')
    )
    allocated_count = sum(
        1 if row.quantity is None else row.quantity for row in allocations
    )
    requested_count = sum(order.lines.values_list('quantity', flat=True))
    cost = Decimal('0')
    unknown = False
    provisional = False
    for allocation in allocations:
        value, is_unknown, is_provisional = _allocated_cost(allocation)
        unknown = unknown or is_unknown
        provisional = provisional or is_provisional
        if value is not None:
            cost += Decimal(value)
    complete = allocated_count == requested_count and not unknown
    cost = money(cost)
    return {
        'allocation_complete': allocated_count == requested_count,
        'cost_complete': not unknown,
        'provisional': provisional,
        'cost_total': f'{cost:f}' if not unknown else None,
        'estimated_margin': f'{money(order.subtotal_ex_tax - cost):f}' if complete else None,
        'currency_code': order.currency_code,
    }
