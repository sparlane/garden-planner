"""Transactional helpers connecting serialized units to physical seed trays."""

from itertools import product

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from .models import SeedTray, SeedTrayCell


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
