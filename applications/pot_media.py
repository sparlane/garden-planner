"""Validate whole-fill media documents and serialize them with pot cleaning."""

from django.core.exceptions import ValidationError
from django.db.models import Q

from inventory.models import InventoryItem
from seedtrays.container_fills import lock_pot_fills
from seedtrays.models import SeedTrayGeneration

from .models import InputApplicationTarget


def validate_pot_media(application, *, lock=False):
    """Keep pot-media documents separate from legacy crop input accounting.

    A line owns one whole fill and records an explicit media quantity. Mixed
    targets would need an allocation basis, and a crop pool would allocate the
    cost before any plant left the fill. Those workflows are not inferred here.
    """
    targets = list(InputApplicationTarget.objects.filter(line__application=application))
    ids = {target.container_fill_id for target in targets if target.container_fill_id}
    if not ids:
        return []
    fills = lock_pot_fills(application.workspace, ids) if lock else list(
        SeedTrayGeneration.objects.filter(workspace=application.workspace, pk__in=ids)
    )
    if len(fills) != len(ids) or application.batch_id:
        raise ValidationError({'container_fill': 'Apply media to fills in this workspace without naming a crop batch.'})
    lines = list(application.lines.prefetch_related('targets').select_related('item'))
    for line in lines:
        selected = list(line.targets.all())
        if len(selected) != 1 or not selected[0].container_fill_id or selected[0].weight != 1:
            raise ValidationError({'targets': 'Each pot media line must name exactly one whole fill with weight 1.'})
        if line.item.category != InventoryItem.Category.GROWING_MEDIA:
            raise ValidationError({'item': 'Pot fills accept growing media only.'})
        if line.usage_basis != InventoryItem.UsageBasis.MANUAL:
            raise ValidationError({'usage_basis': 'Record the explicit media quantity using manual usage.'})
    for fill in fills:
        if fill.tray_id or fill.status != SeedTrayGeneration.Status.OPEN:
            raise ValidationError({'container_fill': 'Apply media to an open pot fill.'})
        if fill.review_state != SeedTrayGeneration.ReviewState.NONE:
            raise ValidationError({'container_fill': 'Review this fill before applying media.'})
        if application.applied_at < fill.opened_at:
            raise ValidationError({'applied_at': 'Media application cannot precede the fill opening.'})
        if fill.inventory_unit_id and fill.inventory_unit.standing_plants.filter(
            Q(ended__isnull=True) | Q(ended__gte=fill.opened_at),
        ).exists():
            raise ValidationError({'container_fill': 'This fill has served plants and needs departure accounting.'})
    return fills
