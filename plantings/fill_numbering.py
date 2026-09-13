"""Give an occupied counted pot an identity without creating a media departure."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.ledger import balance_is_known, lock_lots, quantize_money, unpromised_bulk
from inventory.models import InventoryItem, InventoryUnit
from seedtrays.models import SeedTrayGeneration

from .models import SpecificPlant, SpecificPlantLocation


@transaction.atomic
def number_counted_pot(workspace, user, fill, plant_id):
    """Convert one held pot under the plant, placement, lot and fill locks.

    Keep the placement primary key, original start and fill. They determine
    the fixed media share and its rounding order. Numbering is an audited
    identity change within that stay, not an exit and a second participant.
    """
    plant = SpecificPlant.objects.select_for_update().filter(workspace=workspace, pk=plant_id).first()
    if plant is None:
        raise ValidationError({'plant': 'Choose a plant in this workspace.'})
    placement = plant.locations.select_for_update().filter(container_fill=fill, ended__isnull=True).first()
    if placement is None:
        raise ValidationError({'plant': 'Choose a plant currently standing in this counted fill.'})
    fill = SeedTrayGeneration.objects.filter(workspace=workspace, pk=fill.pk, stock_lot__isnull=False).first()
    if fill is None:
        raise ValidationError({'container_fill': 'Choose a counted pot fill in this workspace.'})
    lot = lock_lots(workspace, [fill.stock_lot_id])[fill.stock_lot_id]
    fill = SeedTrayGeneration.objects.select_for_update().get(pk=fill.pk)
    if placement.numbered_at is not None:
        return placement
    if fill.status != SeedTrayGeneration.Status.OPEN or fill.review_state != SeedTrayGeneration.ReviewState.NONE:
        raise ValidationError({'container_fill': 'Choose an open, reviewed fill.'})
    if lot.item.tracking_mode != InventoryItem.TrackingMode.MIXED or not lot.item.active:
        raise ValidationError({'container_fill': 'Only active mixed-tracking pots can be numbered.'})
    if not fill.source_location.active:
        raise ValidationError({'container_fill': 'The fill location is inactive.'})
    if not balance_is_known(lot) or unpromised_bulk(lot, fill.source_location) < 0:
        raise ValidationError({'container_fill': 'Reconcile the pot stock before numbering this fill.'})
    placement.clean()
    numbered_at = timezone.now()
    if placement.started > numbered_at:
        raise ValidationError({'plant': 'The plant has not yet entered this fill.'})
    unit = InventoryUnit.objects.create(
        workspace=workspace, item=lot.item, source_lot=lot,
        acquisition_cost=None if lot.base_unit_cost is None else quantize_money(lot.base_unit_cost),
        currency_code=lot.currency_code, current_location=fill.source_location, created_by=user,
    )
    # This is the sole exception to placement target immutability. Retain the
    # original interval and record when/by whom its anonymous pot was numbered.
    # No save/departure callback runs: no plant or media left the fill.
    SpecificPlantLocation.objects.filter(pk=placement.pk).update(
        location_type=SpecificPlantLocation.CONTAINER_UNIT, location=None,
        container_unit=unit, numbered_at=numbered_at, numbered_by=user,
    )
    placement.refresh_from_db()
    return placement
