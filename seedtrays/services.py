"""Transactional helpers connecting serialized units to physical seed trays."""

from itertools import product

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from .models import SeedTray, SeedTrayCell


def rename_tray_inventory_item(tray_model):
    """Keep the paired item named after the model it belongs to.

    The item is what the stock screens call these trays, so a corrected name
    has to reach it as well; leaving it behind would have the catalog and the
    inventory disagree about the same tray. The name is not what a tray was
    received as — the grid is, and that is frozen separately — so this stays
    reachable after stock has been posted.
    """
    item = tray_model.inventory_item
    name = tray_model.inventory_item_name
    if item is None or item.name == name:
        return None
    item.name = name
    item.save(update_fields=['name', 'updated'])
    return item


@transaction.atomic
def create_tray_for_unit(unit, notes=''):
    """Create one stable tray and complete cell grid for a mapped unit."""
    try:
        tray_model = unit.item.seed_tray_model
    except ObjectDoesNotExist:
        return None
    if unit.workspace_id != tray_model.workspace_id:
        raise ValidationError({'unit': 'The unit and tray model workspaces differ.'})
    if hasattr(unit, 'seed_tray'):
        raise ValidationError({'unit': 'This unit already has a seed tray.'})
    tray = SeedTray.objects.create(
        workspace=unit.workspace,
        model=tray_model,
        inventory_unit=unit,
        notes=notes,
    )
    cells = [
        SeedTrayCell(tray=tray, x_position=x_position, y_position=y_position)
        for x_position, y_position in product(
            range(tray_model.x_cells),
            range(tray_model.y_cells),
        )
    ]
    SeedTrayCell.objects.bulk_create(cells)
    return tray
