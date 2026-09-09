"""Convert validated application payloads to domain requests for API and bulk work."""

from decimal import Decimal

from rest_framework.exceptions import ValidationError

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from inventory.models import InventoryUnit
from plantings.models import PlantCohort, ProductionBatch, SpecificPlant
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayGeneration

from .models import InputApplicationTarget
from .services import ApplicationRequest, LineRequest, TargetRequest, cells_for_tray

TargetType = InputApplicationTarget.TargetType

#: How each target type is reached, and the lookup that keeps it in workspace.
#: A tray cell is not workspace owned in its own right, so it is scoped through
#: the tray that holds it.
TARGET_SOURCES = {
    TargetType.CONTAINER_FILL: (SeedTrayGeneration, 'workspace'),
    TargetType.BATCH: (ProductionBatch, 'workspace'),
    TargetType.SEED_TRAY_CELL: (SeedTrayCell, 'tray__workspace'),
    TargetType.SPECIFIC_PLANT: (SpecificPlant, 'workspace'),
    TargetType.PLANT_COHORT: (PlantCohort, 'workspace'),
    TargetType.INVENTORY_UNIT: (InventoryUnit, 'workspace'),
    TargetType.GARDEN_AREA: (GardenArea, 'workspace'),
    TargetType.GARDEN_BED: (GardenBed, 'workspace'),
    TargetType.GARDEN_ROW: (GardenRow, 'workspace'),
    TargetType.GARDEN_SQUARE: (GardenSquare, 'workspace'),
}


def _resolve_target(workspace, values):
    """Turn one selected primary key into the object it names."""
    target_type = values['target_type']
    model, lookup = TARGET_SOURCES[target_type]
    try:
        target = model.objects.get(**{lookup: workspace}, pk=values['target'])
    except model.DoesNotExist as exc:
        raise ValidationError({
            'targets': f'No {target_type} with id {values["target"]} in this workspace.',
        }) from exc
    weight = values.get('weight')
    return TargetRequest(
        target_type=target_type,
        target=target,
        weight=1 if weight is None else weight,
    )


def _resolve_tray_cells(workspace, tray_pk):
    """Expand a whole-tray selection into one target per cell."""
    try:
        tray = SeedTray.objects.get(workspace=workspace, pk=tray_pk)
    except SeedTray.DoesNotExist as exc:
        raise ValidationError({
            'tray': f'No tray with id {tray_pk} in this workspace.',
        }) from exc
    cells = cells_for_tray(tray)
    if not cells:
        raise ValidationError({'tray': 'That tray has no cells recorded.'})
    return cells


def build_lines(workspace, line_values):
    """Turn validated line payloads into the service's line requests.

    Every optional key is read with a fallback because a partial update skips
    serializer defaults, so an absent field means "leave it alone" rather than
    an error.
    """
    lines = []
    for line in line_values:
        targets = [
            _resolve_target(workspace, target)
            for target in line.get('targets') or []
        ]
        tray = line.get('tray')
        if tray is not None:
            targets.extend(_resolve_tray_cells(workspace, tray))
        lines.append(LineRequest(
            item=line['item'],
            lot=line['lot'],
            applied_quantity=line['applied_quantity'],
            unit_code=line.get('unit_code'),
            unit_conversion=line.get('unit_conversion'),
            usage_basis=line.get('usage_basis') or '',
            fill_factor=line.get('fill_factor'),
            waste_quantity=line.get('waste_quantity') or Decimal('0'),
            waste_reason=line.get('waste_reason') or '',
            override_reason=line.get('override_reason') or '',
            notes=line.get('notes') or '',
            targets=tuple(targets),
        ))
    return tuple(lines)


def build_request(workspace, values):
    """Turn a validated create payload into the service's request object."""
    return ApplicationRequest(
        applied_at=values['applied_at'],
        source_location=values['source_location'],
        batch=values['batch'],
        notes=values['notes'],
        lines=build_lines(workspace, values['lines']),
    )
