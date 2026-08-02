"""Transactional seed catalog, packet receiving, and counting services."""

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from inventory.ledger import (
    COST_QUANTUM,
    MovementRequest,
    physical_balance,
    post_receipt,
    post_stock_movement,
    quantize_quantity,
)
from inventory.models import (
    InventoryItem,
    InventoryLocation,
    QuantityCertainty,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
    StockLot,
)

from .models import (
    SeedPacket,
    SeedPacketQuantityReconciliation,
    SeedPacketReceiptDraft,
    Seeds,
)


def create_seed_inventory_item(workspace, seeds, base_unit):
    """Create the inventory identity paired with one seed catalog row."""
    return InventoryItem.objects.create(
        workspace=workspace,
        name=str(seeds),
        category=InventoryItem.Category.SEED,
        base_unit=base_unit,
        tracking_mode=InventoryItem.TrackingMode.LOT,
        default_usage_basis=InventoryItem.UsageBasis.MANUAL,
    )


def set_seed_inventory_unit(seeds, base_unit):
    """Change semantic seed units only before packet stock history exists."""
    item = seeds.inventory_item
    if item.stock_history_started_at or seeds.seedpacket_set.filter(
        quantity_reconciliations__isnull=False,
    ).exists():
        raise ValidationError({
            'base_unit': 'Seed units cannot change after packet stock history exists.',
        })
    item.base_unit = base_unit
    item.save(update_fields=['base_unit', 'updated'])
    return item


def _packet_location(workspace):
    token = uuid4().hex.upper()
    return InventoryLocation.objects.create(
        workspace=workspace,
        name=f'Seed packet {token[:8]}',
        code=f'SEED-PACKET-{token}',
        location_type=InventoryLocation.LocationType.SEED_PACKET,
        notes='System-managed seed packet container.',
    )


@transaction.atomic
def create_packet_receipt_draft(workspace, user, values):
    """Create one ordinary receipt line backed by a packet container."""
    seeds = Seeds.objects.select_for_update().select_related(
        'supplier',
    ).get(pk=values['seeds'].pk, workspace=workspace)
    if not seeds.inventory_item_id:
        raise ValidationError({'seeds': 'The seed catalog has no inventory item.'})
    location = _packet_location(workspace)
    receipt = StockReceipt.objects.create(
        workspace=workspace,
        supplier=seeds.supplier,
        received_date=values['received_date'],
        supplier_reference=values.get('supplier_reference', ''),
        currency_code=workspace.currency_code,
        tax_rate=values.get('tax_rate', workspace.default_tax_rate),
        tax_recoverable=values.get('tax_recoverable', False),
        notes=values.get('notes', ''),
        created_by=user,
    )
    quantity = values.get('quantity')
    certainty = values['quantity_certainty']
    base_quantity = None
    if certainty != QuantityCertainty.UNKNOWN:
        base_quantity = quantize_quantity(quantity)
    StockReceiptLine.objects.create(
        receipt=receipt,
        item=seeds.inventory_item,
        supplier_lot_reference=values.get('supplier_lot_reference', ''),
        expires_on=values.get('sow_by'),
        quantity=quantity,
        quantity_certainty=certainty,
        unit_code=seeds.inventory_item.base_unit,
        base_quantity=base_quantity,
        line_cost_ex_tax=values['line_price'],
        destination=location,
    )
    return SeedPacketReceiptDraft.objects.create(
        workspace=workspace,
        seeds=seeds,
        receipt=receipt,
        storage_location=location,
        notes=values.get('notes', ''),
    )


@transaction.atomic
def update_packet_receipt_draft(draft, values):
    """Update an unposted one-line seed packet receipt."""
    draft = SeedPacketReceiptDraft.objects.select_for_update().select_related(
        'receipt',
        'seeds',
    ).get(pk=draft.pk)
    if draft.receipt.status != StockReceipt.Status.DRAFT:
        raise ValidationError({'status': 'Posted packet receipts are immutable.'})
    receipt = draft.receipt
    for field in (
        'received_date',
        'supplier_reference',
        'tax_rate',
        'tax_recoverable',
        'notes',
    ):
        if field in values:
            setattr(receipt, field, values[field])
    receipt.save()
    line = receipt.lines.select_for_update().get()
    for field in ('supplier_lot_reference', 'expires_on', 'line_cost_ex_tax'):
        source = 'sow_by' if field == 'expires_on' else 'line_price' if field == 'line_cost_ex_tax' else field
        if source in values:
            setattr(line, field, values[source])
    if 'quantity_certainty' in values or 'quantity' in values:
        certainty = values.get('quantity_certainty', line.quantity_certainty)
        quantity = values.get('quantity', line.quantity)
        if certainty == QuantityCertainty.UNKNOWN:
            quantity = None
            base_quantity = None
        else:
            base_quantity = quantize_quantity(quantity)
        line.quantity_certainty = certainty
        line.quantity = quantity
        line.base_quantity = base_quantity
    line.save()
    if 'notes' in values:
        draft.notes = values['notes']
        draft.save(update_fields=['notes'])
    return draft


@transaction.atomic
def delete_packet_receipt_draft(draft):
    """Delete an unposted draft and its unused packet container."""
    draft = SeedPacketReceiptDraft.objects.select_for_update().select_related(
        'receipt',
        'storage_location',
    ).get(pk=draft.pk)
    if draft.receipt.status != StockReceipt.Status.DRAFT:
        raise ValidationError({'status': 'Only draft packet receipts can be deleted.'})
    receipt = draft.receipt
    location = draft.storage_location
    draft.delete()
    receipt.delete()
    location.delete()


@transaction.atomic
def post_packet_receipt(draft, user):
    """Post a seed receipt and create its public packet atomically."""
    draft = SeedPacketReceiptDraft.objects.select_for_update().select_related(
        'receipt',
        'seeds',
        'storage_location',
    ).get(pk=draft.pk)
    if draft.packet_id:
        raise ValidationError({'status': 'This packet receipt is already posted.'})
    receipt, lots = post_receipt(draft.receipt, user)
    if len(lots) != 1:
        raise ValidationError({'lines': 'A seed packet receipt requires one line.'})
    lot = lots[0]
    packet = SeedPacket.objects.create(
        workspace=draft.workspace,
        seeds=draft.seeds,
        stock_lot=lot,
        storage_location=draft.storage_location,
        purchase_date=lot.received_on,
        sow_by=lot.expires_on,
        notes=draft.notes,
    )
    draft.packet = packet
    draft.save(update_fields=['packet'])
    return receipt, packet


def _latest_reconciliation(packet):
    return packet.quantity_reconciliations.order_by('-created', '-pk').first()


def packet_quantity_certainty(packet):
    """Return the latest asserted certainty for one packet."""
    reconciliation = _latest_reconciliation(packet)
    if reconciliation:
        return reconciliation.quantity_certainty
    return packet.stock_lot.quantity_certainty


def packet_effective_initial_quantity(packet):
    """Return the latest reconstructed opening quantity, if numeric."""
    reconciliation = _latest_reconciliation(packet)
    if reconciliation:
        return reconciliation.reconstructed_initial_quantity
    return packet.stock_lot.initial_base_quantity


def packet_remaining_quantity(packet):
    """Return a usable numeric balance or None when it remains unknown."""
    if packet_quantity_certainty(packet) == QuantityCertainty.UNKNOWN:
        return None
    return quantize_quantity(physical_balance(
        packet.stock_lot,
        packet.storage_location,
    ))


def _movement_totals(packet):
    movements = StockMovement.objects.filter(
        lot=packet.stock_lot,
    )
    consumed = movements.filter(
        movement_type=StockMovement.MovementType.CONSUMPTION,
        source=packet.storage_location,
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    reversed_consumption = movements.filter(
        movement_type=StockMovement.MovementType.REVERSAL,
        destination=packet.storage_location,
        reversal_of__movement_type=StockMovement.MovementType.CONSUMPTION,
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    adjustments = movements.filter(
        Q(movement_type=StockMovement.MovementType.ADJUSTMENT_GAIN) | Q(movement_type=StockMovement.MovementType.ADJUSTMENT_LOSS),
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    return (
        quantize_quantity(consumed - reversed_consumption),
        quantize_quantity(adjustments),
    )


def packet_inventory_snapshot(packet):
    """Return truthful packet balance, valuation, and warning metadata."""
    certainty = packet_quantity_certainty(packet)
    initial = packet_effective_initial_quantity(packet)
    remaining = packet_remaining_quantity(packet)
    sown, adjustments = _movement_totals(packet)
    unit_cost = None
    if initial and packet.stock_lot.acquisition_total is not None:
        unit_cost = (packet.stock_lot.acquisition_total / initial).quantize(
            COST_QUANTUM,
        )
    warnings = []
    if certainty == QuantityCertainty.UNKNOWN:
        warnings.append('Packet quantity and remaining balance are unknown.')
    elif certainty == QuantityCertainty.ESTIMATED:
        warnings.append('Packet quantity and valuation are estimated.')
    if packet.stock_lot.acquisition_total is None:
        warnings.append('Packet cost is unknown.')
    empty = None
    if certainty == QuantityCertainty.EXACT:
        empty = remaining == 0
    return {
        'lot': packet.stock_lot_id,
        'location': packet.storage_location_id,
        'quantity_certainty': certainty,
        'received_quantity': initial,
        'sown_quantity': sown,
        'adjustment_quantity': adjustments,
        'remaining_quantity': remaining,
        'base_unit': packet.stock_lot.item.base_unit,
        'acquisition_total': packet.stock_lot.acquisition_total,
        'effective_base_unit_cost': unit_cost,
        'currency_code': packet.stock_lot.currency_code,
        'empty': empty,
        'warnings': warnings,
    }


@transaction.atomic
def reconcile_packet_quantity(packet, user, count, certainty, reason):
    """Record a count and bring the packet's numeric ledger balance to it."""
    if certainty == QuantityCertainty.UNKNOWN:
        raise ValidationError({
            'quantity_certainty': 'A physical count must be exact or estimated.',
        })
    packet = SeedPacket.objects.select_for_update().get(pk=packet.pk)
    if not packet.stock_lot_id or not packet.storage_location_id:
        raise ValidationError({'packet': 'The packet has no inventory identity.'})
    packet.stock_lot = StockLot.objects.select_for_update().select_related(
        'item',
    ).get(pk=packet.stock_lot_id)
    counted = quantize_quantity(count)
    if counted < 0:
        raise ValidationError({'counted_quantity': 'Quantity cannot be negative.'})
    raw_balance = quantize_quantity(physical_balance(
        packet.stock_lot,
        packet.storage_location,
    ))
    previous_initial = packet_effective_initial_quantity(packet)
    delta = quantize_quantity(counted - raw_balance)
    reconstructed = quantize_quantity((previous_initial or Decimal('0')) + delta)
    if reconstructed < 0:
        raise ValidationError({
            'counted_quantity': 'The count cannot produce a negative opening quantity.',
        })
    movement = None
    if delta:
        movement_type = StockMovement.MovementType.ADJUSTMENT_GAIN
        source = None
        destination = packet.storage_location
        if delta < 0:
            movement_type = StockMovement.MovementType.ADJUSTMENT_LOSS
            source = packet.storage_location
            destination = None
        movement = post_stock_movement(
            packet.workspace,
            user,
            MovementRequest(
                lot=packet.stock_lot,
                movement_type=movement_type,
                quantity=abs(delta),
                source=source,
                destination=destination,
                reason=reason,
                reference=f'Seed packet {packet.pk} physical count',
            ),
        )
    reconciliation = SeedPacketQuantityReconciliation.objects.create(
        workspace=packet.workspace,
        packet=packet,
        counted_quantity=counted,
        quantity_certainty=certainty,
        reconstructed_initial_quantity=reconstructed,
        movement=movement,
        reason=reason,
        recorded_by=user,
    )
    packet.stock_lot.item.mark_stock_history_started(timezone.now())
    return reconciliation
