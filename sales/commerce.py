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


def recompute_order_status(order):
    """Derive fulfillment status from effective dispatch and return facts."""
    order = SalesOrder.objects.select_for_update(of=('self',)).get(pk=order.pk)
    if order.status == SalesOrder.Status.CANCELLED:
        return order
    returned = _effective_return_line_ids(order)
    fulfilled = _effective_fulfillment_lines(order).exclude(pk__in=returned).count()
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


def _available_positions(order):
    returned = _effective_return_line_ids(order)
    occupied = {}
    for row in _effective_fulfillment_lines(order).exclude(pk__in=returned):
        occupied.setdefault(row.allocation.line_id, set()).add(row.commercial_position)
    return {
        line.pk: [
            position for position in range(1, line.quantity + 1)
            if position not in occupied.get(line.pk, set())
        ]
        for line in order.lines.all()
    }


def _plant_cost(plant):
    breakdown = plant_cost_breakdown(plant)
    value = breakdown['provisional_value'] or breakdown['final_value']
    return (
        Decimal(value) if value is not None else None,
        bool(breakdown['provisional']),
    )


def _validate_tray_riders(units, selected_plant_ids):
    for unit in units.values():
        try:
            tray = unit.seed_tray
        except ObjectDoesNotExist:
            continue
        riders = set(SpecificPlantLocation.objects.filter(
            seed_tray_cell__tray=tray, ended__isnull=True,
        ).values_list('specific_plant_id', flat=True))
        if not riders.issubset(selected_plant_ids):
            raise ValidationError({
                'allocations': f'Tray {tray.pk} still carries plants not in this fulfillment.',
            })


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
        'line', 'plant__batch', 'inventory_unit__item',
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
    lot_ids = [row['lot'].pk for row in packaging]
    lots = lock_lots(order.workspace, lot_ids)
    _validate_tray_riders(units, set(plant_ids))
    positions = _available_positions(order)
    fulfillment = Fulfillment.objects.create(
        workspace=order.workspace, order=order,
        fulfillment_number=_number(order.workspace), fulfilled_at=fulfilled_at,
        notes=notes.strip(), operation_key=operation_key,
        request_fingerprint=fingerprint, created_by=_actor(user),
    )
    for allocation in allocations:
        available = positions[allocation.line_id]
        if not available:
            raise ValidationError({'allocations': 'A line has no remaining quantity to fulfill.'})
        position = available.pop(0)
        amounts = line_position_amounts(allocation.line)[position]
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
        FulfillmentLine.objects.create(
            fulfillment=fulfillment, allocation=allocation,
            commercial_position=position, cogs_amount=cogs_amount,
            cogs_provisional=provisional, currency_code=order.currency_code,
            lifecycle_event=lifecycle_event, stock_movement=stock_movement,
            **amounts,
        )
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
                   external_reference='', notes=''):
    """Record operational cash independently from fulfillment timing."""
    amount = money(amount)
    payload = {
        'order': order.pk, 'paid_on': paid_on, 'amount': amount,
        'method': method, 'external_reference': external_reference, 'notes': notes,
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
        external_reference=external_reference.strip(), notes=notes.strip(),
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )


def _return_event(outcome):
    return {
        SalesReturnLine.Outcome.AVAILABLE: EventType.RETURNED_AVAILABLE,
        SalesReturnLine.Outcome.QUARANTINED: EventType.RETURNED_QUARANTINED,
        SalesReturnLine.Outcome.DISCARDED: EventType.RETURNED_DISCARDED,
    }[outcome]


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
        .select_related('allocation__plant', 'allocation__inventory_unit')
        .filter(fulfillment__order=order, fulfillment__reversal__isnull=True,
                pk__in=line_ids).order_by('pk')
    }
    if len(lines) != len(set(line_ids)):
        raise ValidationError({'items': 'One or more fulfillment lines are unavailable.'})
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
        if allocation.plant_id:
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
                amount, reason, refunded_at=None, sales_return=None, notes=''):
    """Refund paid value and classify it against original recognized lines."""
    requested_at = refunded_at
    refunded_at = refunded_at or timezone.now()
    amount = money(amount)
    line_ids = sorted(set(line.pk for line in fulfillment_lines))
    payload = {
        'order': order.pk, 'payment': payment.pk, 'lines': line_ids,
        'amount': amount, 'reason': reason, 'refunded_at': requested_at,
        'sales_return': getattr(sales_return, 'pk', None), 'notes': notes,
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
        operation_key=operation_key, request_fingerprint=fingerprint,
        created_by=_actor(user),
    )
    RefundLine.objects.bulk_create([
        RefundLine(refund=refund, fulfillment_line=share.pop('line'), **share)
        for share in shares
    ])
    return refund


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
    ).get(pk=original.pk, reversal_of__isnull=True)
    if hasattr(original, 'reversal'):
        raise ValidationError({'fulfillment': 'This fulfillment is already reversed.'})
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
    original = Payment.objects.select_for_update(of=('self',)).get(
        pk=original.pk, reversal_of__isnull=True,
    )
    if hasattr(original, 'reversal'):
        raise ValidationError({'payment': 'This payment is already reversed.'})
    if _effective(original.refunds.all()).exists():
        raise ValidationError({'payment': 'Reverse linked refunds first.'})
    return Payment.objects.create(
        workspace=original.workspace, order=original.order,
        paid_on=occurred_at.date(), amount=original.amount,
        currency_code=original.currency_code, method=original.method,
        external_reference=original.external_reference, notes=reason.strip(),
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
    original = Refund.objects.select_for_update(of=('self',)).get(
        pk=original.pk, reversal_of__isnull=True,
    )
    if hasattr(original, 'reversal'):
        raise ValidationError({'refund': 'This refund is already reversed.'})
    return Refund.objects.create(
        workspace=original.workspace, order=original.order,
        payment=original.payment, sales_return=original.sales_return,
        refunded_at=occurred_at, amount=original.amount,
        currency_code=original.currency_code, reason=reason.strip(),
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
    ).get(pk=original.pk, reversal_of__isnull=True)
    if hasattr(original, 'reversal'):
        raise ValidationError({'sales_return': 'This return is already reversed.'})
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
    if original.quarantine_case_id:
        act_on_quarantine(
            original.workspace, user, original.quarantine_case,
            action_name='release', idempotency_key=uuid5(operation_key, 'release'),
            reason=reason, occurred_at=occurred_at,
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
    recompute_order_status(original.order)
    return reversal
