"""Transactional commands for sales orders and exact reservations."""

# pylint: disable=too-many-locals

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from costing.services import plant_cost_breakdown
from health.availability import is_quarantined
from inventory.ledger import lock_units, unit_physical_state
from inventory.models import InventoryUnit
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


def _target_error(line, target):
    """Return why a locked target cannot currently satisfy this line."""
    if line.line_type == SalesOrderLine.LineType.SEEDLING:
        if target.batch.variety_id != line.variety_id:
            return 'wrong_variety'
        if plant_lifecycle_summary(target).state not in SELLABLE_STATES:
            return 'not_sellable'
        if is_quarantined(target):
            return 'quarantined'
        reserved = SalesOrderAllocation.objects.filter(
            plant=target,
            status=SalesOrderAllocation.Status.RESERVED,
        ).exclude(line=line).exists()
    else:
        if target.item_id != line.tray_item_id:
            return 'wrong_item'
        if unit_physical_state(target) != 'available':
            return 'not_available'
        reserved = SalesOrderAllocation.objects.filter(
            inventory_unit=target,
            status=SalesOrderAllocation.Status.RESERVED,
        ).exclude(line=line).exists()
    return 'already_reserved' if reserved else None


def _target_allocations(line, target, statuses):
    """Return other active claims for a target, with readable order context."""
    identity = (
        {'plant': target}
        if line.line_type == SalesOrderLine.LineType.SEEDLING
        else {'inventory_unit': target}
    )
    return (
        SalesOrderAllocation.objects
        .filter(**identity, status__in=statuses)
        .exclude(line=line)
        .select_related('line__order')
        .order_by('line__order__order_number', 'pk')
    )


def _allocation_reference(allocation):
    """Describe the competing promise without exposing mutable line details."""
    return {
        'order': allocation.line.order_id,
        'order_number': allocation.line.order.order_number,
        'status': allocation.status,
    }


def preview_targets(line, plant_ids=(), unit_ids=()):
    """Resolve explicit target IDs to compatible selections and conflicts."""
    workspace = line.order.workspace
    if line.line_type == SalesOrderLine.LineType.SEEDLING and unit_ids:
        raise ValidationError({'units': 'A seedling line accepts plants only.'})
    if line.line_type == SalesOrderLine.LineType.TRAY and plant_ids:
        raise ValidationError({'plants': 'A tray line accepts serialized units only.'})
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


@transaction.atomic
def allocate_targets(line, user, plant_ids=(), unit_ids=(), expires_at=None):
    """Add tentative targets, or immediately reserve confirmed replacements."""
    order = SalesOrder.objects.select_for_update().get(pk=line.order_id)
    line = SalesOrderLine.objects.select_related('order').get(pk=line.pk, order=order)
    allowed = {
        SalesOrder.Status.QUOTE,
        SalesOrder.Status.DRAFT,
        SalesOrder.Status.CONFIRMED,
        SalesOrder.Status.PARTIALLY_FULFILLED,
    }
    if order.status not in allowed:
        raise ValidationError({'status': 'This order cannot accept allocations.'})
    ids = sorted(set(plant_ids or unit_ids))
    targets = (
        _locked_plants(order.workspace, ids)
        if line.line_type == SalesOrderLine.LineType.SEEDLING
        else lock_units(order.workspace, ids)
    )
    active_count = line.allocations.filter(
        status__in=[SalesOrderAllocation.Status.PENDING, SalesOrderAllocation.Status.RESERVED],
    ).count()
    if active_count + len(ids) > line.quantity:
        raise ValidationError({'allocations': 'Allocations cannot exceed the requested quantity.'})
    immediate = order.status in {SalesOrder.Status.CONFIRMED, SalesOrder.Status.PARTIALLY_FULFILLED}
    created = []
    try:
        for target_id in ids:
            target = targets[target_id]
            reason = _target_error(line, target)
            if reason:
                raise ValidationError({'allocations': f'Target {target_id}: {reason}.'})
            allocation = SalesOrderAllocation.objects.create(
                line=line,
                plant=target if line.line_type == SalesOrderLine.LineType.SEEDLING else None,
                inventory_unit=target if line.line_type == SalesOrderLine.LineType.TRAY else None,
                status=SalesOrderAllocation.Status.RESERVED if immediate else SalesOrderAllocation.Status.PENDING,
                expires_at=expires_at,
                created_by=user,
            )
            if immediate:
                _event(allocation, ReservationEvent.EventType.RESERVED, user)
            created.append(allocation)
    except IntegrityError as exc:
        raise ValidationError({'allocations': 'One or more targets were reserved concurrently.'}) from exc
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
        line_pending = [allocation for allocation in line.allocations.all() if allocation.status == SalesOrderAllocation.Status.PENDING]
        if len(line_pending) != line.quantity:
            raise ValidationError({'lines': f'Line {line.pk} requires exactly {line.quantity} allocations.'})
    plant_ids = [allocation.plant_id for allocation in pending if allocation.plant_id]
    unit_ids = [allocation.inventory_unit_id for allocation in pending if allocation.inventory_unit_id]
    plants = _locked_plants(order.workspace, plant_ids)
    units = lock_units(order.workspace, unit_ids)
    try:
        for allocation in sorted(pending, key=lambda row: row.pk):
            target = plants[allocation.plant_id] if allocation.plant_id else units[allocation.inventory_unit_id]
            reason = _target_error(allocation.line, target)
            if reason:
                holder = _target_allocations(
                    allocation.line,
                    target,
                    [SalesOrderAllocation.Status.RESERVED],
                ).first()
                held_by = f' by {holder.line.order.order_number}' if holder else ''
                raise ValidationError({
                    'allocations': f'Target {target.pk}: {reason}{held_by}.',
                })
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


def order_margin(order):
    """Return an ex-tax margin only when every allocated cost is known."""
    allocations = list(
        SalesOrderAllocation.objects.filter(
            line__order=order,
            status__in=[SalesOrderAllocation.Status.PENDING, SalesOrderAllocation.Status.RESERVED, SalesOrderAllocation.Status.FULFILLED],
        ).select_related('plant__batch', 'inventory_unit')
    )
    allocated_count = len(allocations)
    requested_count = sum(order.lines.values_list('quantity', flat=True))
    cost = Decimal('0')
    unknown = False
    provisional = False
    for allocation in allocations:
        if allocation.plant_id:
            breakdown = plant_cost_breakdown(allocation.plant)
            unknown = unknown or breakdown['unknown_cost']
            provisional = provisional or breakdown['provisional']
            value = breakdown['provisional_value'] or breakdown['final_value']
        else:
            value = allocation.inventory_unit.acquisition_cost
            unknown = unknown or value is None
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
