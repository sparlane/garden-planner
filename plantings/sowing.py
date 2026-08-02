"""Atomic inventory posting and correction services for seed sowings."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from inventory.ledger import (
    MovementRequest,
    correct_stock_movement,
    post_stock_movement,
)
from inventory.models import QuantityCertainty, StockMovement
from seeds.services import (
    ensure_packet_inventory_identity,
    packet_quantity_certainty,
)

from .models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    SeedTrayPlanting,
    SowingStockPosting,
)


def _planting_link(planting):
    """Return the explicit audit-link field for one concrete sowing."""
    if isinstance(planting, GardenRowDirectSowPlanting):
        return {'row_planting': planting}
    if isinstance(planting, GardenSquareDirectSowPlanting):
        return {'square_planting': planting}
    if isinstance(planting, SeedTrayPlanting):
        return {'tray_planting': planting}
    raise ValidationError({'planting': 'Select a supported sowing type.'})


def _movement_request(planting, packet, quantity, reason='', correction=False):
    """Build packet-container consumption intent for the ledger."""
    unknown = packet_quantity_certainty(packet) == QuantityCertainty.UNKNOWN
    return MovementRequest(
        lot=packet.stock_lot,
        movement_type=StockMovement.MovementType.CONSUMPTION,
        quantity=Decimal(quantity),
        source=packet.storage_location,
        occurred_at=None if correction else planting.planted,
        reason=reason,
        reference=f'{planting._meta.label} {planting.pk}',
        enforce_source_balance=not unknown,
    )


@transaction.atomic
def post_sowing_consumption(planting, user):
    """Consume the selected packet quantity and link it to a new sowing."""
    packet = ensure_packet_inventory_identity(planting.seeds_used)
    if packet.workspace_id != planting.workspace_id:
        raise ValidationError({
            'seeds_used': 'The packet belongs to a different workspace.',
        })
    movement = post_stock_movement(
        planting.workspace,
        user,
        _movement_request(planting, packet, planting.quantity),
    )
    posting = SowingStockPosting.objects.create(
        workspace=planting.workspace,
        movement=movement,
        **_planting_link(planting),
    )
    packet.stock_lot.item.mark_stock_history_started(movement.occurred_at)
    return posting


def _current_consumption(planting):
    """Return the latest unreplaced consumption posting."""
    return planting.stock_postings.filter(
        movement__movement_type=StockMovement.MovementType.CONSUMPTION,
        replacement__isnull=True,
    ).select_related('movement__lot').order_by('-created', '-pk').first()


def _validate_tray_quantity(planting, quantity):
    if not isinstance(planting, SeedTrayPlanting):
        return
    allocated = planting.cell_plantings.aggregate(total=Sum('quantity'))['total'] or 0
    if quantity < allocated:
        raise ValidationError({
            'quantity': 'Quantity cannot be below the existing cell allocation total.',
        })


@transaction.atomic
def correct_sowing_consumption(
    planting,
    user,
    seeds_used=None,
    quantity=None,
    reason='',
):
    """Reverse and replace a sowing's current packet consumption."""
    planting = type(planting).objects.select_for_update().select_related(
        'seeds_used',
    ).get(pk=planting.pk)
    current = _current_consumption(planting)
    if current is None:
        raise ValidationError({
            'planting': 'Historical sowings without stock postings cannot be corrected.',
        })
    packet = ensure_packet_inventory_identity(seeds_used or planting.seeds_used)
    corrected_quantity = quantity if quantity is not None else planting.quantity
    if corrected_quantity <= 0:
        raise ValidationError({'quantity': 'Quantity must be greater than zero.'})
    if packet.pk == planting.seeds_used_id and corrected_quantity == planting.quantity:
        raise ValidationError({'detail': 'Change the packet or quantity.'})
    _validate_tray_quantity(planting, corrected_quantity)
    reversal, replacement = correct_stock_movement(
        current.movement,
        user,
        _movement_request(
            planting,
            packet,
            corrected_quantity,
            reason=reason,
            correction=True,
        ),
        reason,
    )
    link = _planting_link(planting)
    SowingStockPosting.objects.create(
        workspace=planting.workspace,
        movement=reversal,
        **link,
    )
    SowingStockPosting.objects.create(
        workspace=planting.workspace,
        movement=replacement,
        replacement_of=current,
        **link,
    )
    planting.seeds_used = packet
    planting.quantity = corrected_quantity
    planting.save(update_fields=['seeds_used', 'quantity'])
    packet.stock_lot.item.mark_stock_history_started(replacement.occurred_at)
    return {
        'planting': planting,
        'original_movement': current.movement_id,
        'reversal_movement': reversal.pk,
        'replacement_movement': replacement.pk,
    }
