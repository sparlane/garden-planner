"""Small dependency-free model builders shared by API tests."""
# These deliberately mirror the shape of the real creation paths they stand
# in for, so they read the same way; the overlap is the point.
# pylint: disable=duplicate-code
from datetime import date
from decimal import Decimal
from itertools import count

from django.utils import timezone

from garden.models import (
    GardenArea,
    GardenBed,
    GardenGeometryConfirmation,
    GardenRow,
    GardenSquare,
)
from inventory.models import (
    InventoryItem,
    InventoryUnit,
    StockLot,
    StockMovement,
)
from inventory.units import UnitCode
from locations.models import Location
from plantings.models import (
    GardenPlanting,
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    Harvest,
    HarvestPlant,
    PlantLifecycleEvent,
    ProductionBatch,
    ProductionBatchTransition,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import (
    SeedTray,
    SeedTrayCell,
    SeedTrayGeneration,
    SeedTrayGenerationEvent,
    SeedTrayModel,
)
from supplies.models import Supplier


_SEQUENCE = count(1)


def _next_name(prefix):
    return f'{prefix} {next(_SEQUENCE)}'


def make_supplier(**overrides):
    """Create a supplier with valid defaults."""
    values = {
        'name': _next_name('Supplier'),
        'website': 'https://supplier.example.com',
        'notes': 'Shared test supplier',
    }
    values.update(overrides)
    return Supplier.objects.create(**values)


def make_plant_family(**overrides):
    """Create a plant family with valid defaults."""
    values = {
        'name': _next_name('Family'),
        'notes': 'Shared test family',
    }
    values.update(overrides)
    return PlantFamily.objects.create(**values)


def make_plant(**overrides):
    """Create a plant with a family when one is not supplied."""
    values = {
        'name': _next_name('Plant'),
        'notes': 'Shared test plant',
    }
    if 'family' not in overrides:
        values['family'] = make_plant_family()
    values.update(overrides)
    return Plant.objects.create(**values)


def make_plant_variety(**overrides):
    """Create a plant variety with a plant when one is not supplied."""
    values = {
        'name': _next_name('Variety'),
        'notes': 'Shared test variety',
    }
    if 'plant' not in overrides:
        values['plant'] = make_plant()
    values.update(overrides)
    return PlantVariety.objects.create(**values)


def make_seeds(**overrides):
    """Create a seed product with its supplier and variety graph."""
    values = {
        'supplier_code': _next_name('CODE'),
        'url': 'https://supplier.example.com/seeds',
        'notes': 'Shared test seeds',
    }
    if 'supplier' not in overrides:
        values['supplier'] = make_supplier()
    if 'plant_variety' not in overrides:
        values['plant_variety'] = make_plant_variety()
    values.update(overrides)
    return Seeds.objects.create(**values)


def make_seed_packet(**overrides):
    """Create a seed packet and its seed product graph."""
    values = {
        'notes': 'Shared test packet',
    }
    if 'seeds' not in overrides:
        values['seeds'] = make_seeds()
    values.update(overrides)
    return SeedPacket.objects.create(**values)


def make_location(**overrides):
    """Create a physical location that can hold stock, trays, or plants."""
    values = {
        'name': _next_name('Location'),
        'code': _next_name('LOC').replace(' ', '-').upper(),
        'location_type': Location.LocationType.STORAGE,
    }
    values.update(overrides)
    return Location.objects.create(**values)


def make_inventory_item(**overrides):
    """Create a lot-tracked catalog item measured in litres."""
    values = {
        'name': _next_name('Item'),
        'category': InventoryItem.Category.GROWING_MEDIA,
        'base_unit': UnitCode.LITRE,
        'tracking_mode': InventoryItem.TrackingMode.LOT,
        'default_usage_basis': InventoryItem.UsageBasis.MANUAL,
    }
    values.update(overrides)
    return InventoryItem.objects.create(**values)


def make_stock_lot(**overrides):
    """Create a lot holding an opening balance at one location.

    `quantity` and `location` shape the opening movement rather than the lot,
    so a test can stock a known amount somewhere it can then draw from.
    """
    quantity = Decimal(overrides.pop('quantity', '100'))
    location = overrides.pop('location', None)
    values = {
        'origin': StockLot.Origin.OPENING,
        'received_on': date(2026, 1, 1),
        'initial_base_quantity': quantity,
        'acquisition_total': Decimal('0'),
        'base_unit_cost': Decimal('0'),
    }
    if 'item' not in overrides:
        values['item'] = make_inventory_item()
    values.update(overrides)
    workspace = values.setdefault('workspace', values['item'].workspace)
    values.setdefault('currency_code', workspace.currency_code)
    lot = StockLot.objects.create(**values)
    if location is None:
        location = make_location(workspace=workspace)
    StockMovement.objects.create(
        workspace=workspace,
        lot=lot,
        movement_type=StockMovement.MovementType.OPENING,
        quantity=quantity,
        destination=location,
        occurred_at=timezone.now(),
    )
    return lot


def make_garden_area(**overrides):
    """Create a garden area with drawable dimensions."""
    values = {
        'name': _next_name('Area'),
        'size_x': 100,
        'size_y': 100,
    }
    values.update(overrides)
    return GardenArea.objects.create(**values)


def _parent_workspace(overrides):
    """Build a parent in the same workspace the child was asked for.

    Geometry refuses to sit in a different workspace from its parent, so a
    factory that invented the parent in the default workspace would make an
    unsaveable record every time a test asked for a foreign one.
    """
    workspace = overrides.get('workspace')
    return {} if workspace is None else {'workspace': workspace}


def make_garden_geometry_confirmation(**overrides):
    """Confirm what one area's grid step physically measures."""
    values = {
        'length_unit': GardenGeometryConfirmation.LengthUnit.MILLIMETRE,
        'cell_length': Decimal('1'),
    }
    if 'area' not in overrides:
        values['area'] = make_garden_area(**_parent_workspace(overrides))
    values.update(overrides)
    return GardenGeometryConfirmation.objects.create(**values)


def make_garden_bed(**overrides):
    """Create a garden bed and its area."""
    values = {
        'name': _next_name('Bed'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 50,
        'size_y': 50,
    }
    if 'area' not in overrides:
        values['area'] = make_garden_area(**_parent_workspace(overrides))
    values.update(overrides)
    return GardenBed.objects.create(**values)


def make_garden_row(**overrides):
    """Create a garden row and its parent geometry."""
    values = {
        'name': _next_name('Row'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 10,
        'size_y': 1,
    }
    if 'bed' not in overrides:
        values['bed'] = make_garden_bed(**_parent_workspace(overrides))
    values.update(overrides)
    return GardenRow.objects.create(**values)


def make_garden_square(**overrides):
    """Create a garden square and its parent geometry."""
    values = {
        'name': _next_name('Square'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 1,
        'size_y': 1,
    }
    if 'bed' not in overrides:
        values['bed'] = make_garden_bed(**_parent_workspace(overrides))
    values.update(overrides)
    return GardenSquare.objects.create(**values)


def make_garden_planting(**overrides):
    """Create a source-neutral garden planting at one square."""
    values = {
        'source': GardenPlanting.Source.EXISTING_UNKNOWN,
        'tracking': GardenPlanting.Tracking.AGGREGATE,
        'quantity': 1,
        'recorded_on': date(2026, 1, 1),
        'date_basis': GardenPlanting.DateBasis.FIRST_OBSERVED,
    }
    if 'batch' not in overrides:
        values['batch'] = make_production_batch(**_parent_workspace(overrides))
    if 'garden_square' not in overrides and 'location' not in overrides:
        values['garden_square'] = make_garden_square(**_parent_workspace(overrides))
    values.update(overrides)
    return GardenPlanting.objects.create(**values)


def make_seed_tray_model(**overrides):
    """Create a seed-tray model with a two-by-two grid."""
    values = {
        'identifier': _next_name('Tray model'),
        'description': 'Shared test tray model',
        'height': 10,
        'x_size': 20,
        'y_size': 20,
        'x_cells': 2,
        'y_cells': 2,
        'cell_size_ml': 40,
    }
    values.update(overrides)
    return SeedTrayModel.objects.create(**values)


def make_seed_tray(**overrides):
    """Create an audited seed tray without implicitly creating cells."""
    values = {
        'notes': 'Shared test tray',
    }
    if 'model' not in overrides:
        values['model'] = make_seed_tray_model()
    values.update(overrides)
    tray_model = values['model']
    workspace = values.get('workspace', tray_model.workspace)
    location, _created = Location.objects.get_or_create(
        workspace=workspace,
        code='TEST-TRAY-STOCK',
        defaults={
            'name': 'Test tray stock',
            'location_type': Location.LocationType.STORAGE,
        },
    )
    lot = StockLot.objects.create(
        workspace=workspace,
        item=tray_model.inventory_item,
        origin=StockLot.Origin.OPENING,
        received_on=date(2026, 1, 1),
        initial_base_quantity=Decimal('1'),
        acquisition_total=Decimal('0'),
        base_unit_cost=Decimal('0'),
        currency_code=workspace.currency_code,
    )
    unit = InventoryUnit.objects.create(
        workspace=workspace,
        item=tray_model.inventory_item,
        source_lot=lot,
        acquisition_cost=Decimal('0'),
        currency_code=workspace.currency_code,
        current_location=location,
    )
    StockMovement.objects.create(
        workspace=workspace,
        lot=lot,
        unit=unit,
        movement_type=StockMovement.MovementType.OPENING,
        quantity=Decimal('1'),
        destination=location,
        occurred_at='2026-01-01T00:00:00Z',
    )
    values['inventory_unit'] = unit
    return SeedTray.objects.create(**values)


def make_seed_tray_cell(**overrides):
    """Create a seed-tray cell and its parent tray."""
    values = {
        'x_position': 0,
        'y_position': 0,
    }
    if 'tray' not in overrides:
        values['tray'] = make_seed_tray()
    values.update(overrides)
    return SeedTrayCell.objects.create(**values)


def make_seed_tray_generation(**overrides):
    """Create an open tray generation and its opening event."""
    values = dict(overrides)
    if 'tray' not in values:
        values['tray'] = make_seed_tray()
    tray = values['tray']
    sequence = values.setdefault(
        'sequence',
        SeedTrayGeneration.objects.filter(tray=tray).count() + 1,
    )
    values.setdefault('code', f'TRAY-{tray.pk}-{sequence}')
    values.setdefault('opened_at', timezone.now())
    values.setdefault('notes', 'Shared test generation')
    generation = SeedTrayGeneration.objects.create(**values)
    SeedTrayGenerationEvent.objects.create(
        generation=generation,
        event_type=SeedTrayGenerationEvent.EventType.OPENED,
        occurred_at=generation.opened_at,
        reason='Created for tests.',
    )
    return generation


def make_production_batch(**overrides):
    """Create an active production batch and its opening transition."""
    values = {
        'code': _next_name('BATCH').replace(' ', '-').upper(),
        'status': ProductionBatch.Status.ACTIVE,
        'notes': 'Shared test batch',
    }
    if 'variety' not in overrides:
        values['variety'] = make_plant_variety()
    values.update(overrides)
    if values['status'] == ProductionBatch.Status.ACTIVE and 'actual_start' not in values:
        values['actual_start'] = timezone.now()
    batch = ProductionBatch.objects.create(**values)
    ProductionBatchTransition.objects.create(
        batch=batch,
        previous_status='',
        new_status=batch.status,
        reason='Created for tests.',
    )
    return batch


def make_batch_for_packet(packet, **overrides):
    """Create an active batch whose variety matches one seed packet."""
    values = {
        'variety': packet.seeds.plant_variety,
        'workspace': packet.workspace,
    }
    values.update(overrides)
    return make_production_batch(**values)


def _sowing_values(overrides, defaults):
    """Fill in a packet and a variety-matched active batch for a sowing."""
    values = dict(defaults)
    if 'seeds_used' not in overrides:
        values['seeds_used'] = make_seed_packet()
    values.update(overrides)
    if 'batch' not in values:
        values['batch'] = make_batch_for_packet(values['seeds_used'])
    return values


def make_seed_tray_planting(**overrides):
    """Create a seed-tray planting and its related seed and tray graph.

    An open generation on the tray is joined when there is one, and none is
    opened when there is not, so a test that never filled its tray keeps the
    unlinked shape sowings had before generations existed.
    """
    values = _sowing_values(overrides, {
        'quantity': 2,
        'notes': 'Shared test tray planting',
    })
    if 'seed_tray' not in values:
        values['seed_tray'] = make_seed_tray()
    if 'generation' not in values and values['seed_tray'] is not None:
        values['generation'] = SeedTrayGeneration.objects.filter(
            tray=values['seed_tray'],
            status=SeedTrayGeneration.Status.OPEN,
        ).first()
    return SeedTrayPlanting.objects.create(**values)


def make_garden_row_sowing(**overrides):
    """Create a direct-sow row planting with a variety-matched batch."""
    values = _sowing_values(overrides, {
        'quantity': 2,
        'notes': 'Shared test row sowing',
    })
    if 'location' not in values:
        values['location'] = make_garden_row()
    return GardenRowDirectSowPlanting.objects.create(**values)


def make_garden_square_sowing(**overrides):
    """Create a direct-sow square planting with a variety-matched batch."""
    values = _sowing_values(overrides, {
        'quantity': 2,
        'notes': 'Shared test square sowing',
    })
    if 'location' not in values:
        values['location'] = make_garden_square()
    return GardenSquareDirectSowPlanting.objects.create(**values)


def make_seed_tray_cell_planting(**overrides):
    """Create a cell allocation whose cell belongs to its planting's tray."""
    values = dict(overrides)
    cell = values.pop('cell', None)
    planting = values.pop('seed_tray_planting', None)
    if planting is None:
        if cell is None:
            cell = make_seed_tray_cell()
        planting = make_seed_tray_planting(seed_tray=cell.tray)
    elif cell is None:
        cell = make_seed_tray_cell(tray=planting.seed_tray)
    values = {
        'seed_tray_planting': planting,
        'cell': cell,
        'quantity': 2,
        **values,
    }
    return SeedTrayCellPlanting.objects.create(**values)


def make_specific_plant(**overrides):
    """Create a specific plant without implicitly adding location history."""
    values = {
        'notes': 'Shared test specific plant',
    }
    if 'cell_planting' not in overrides:
        values['cell_planting'] = make_seed_tray_cell_planting()
    values.update(overrides)
    return SpecificPlant.objects.create(**values)


def make_plant_lifecycle_event(**overrides):
    """Create one lifecycle fact, defaulting to the germination of a plant."""
    values = dict(overrides)
    plant = values.pop('plant', None) or make_specific_plant()
    defaults = {
        'plant': plant,
        'batch': plant.cell_planting.seed_tray_planting.batch,
        'event_type': PlantLifecycleEvent.EventType.GERMINATED,
        'occurred_at': plant.germinated,
    }
    defaults.update(values)
    return PlantLifecycleEvent.objects.create(**defaults)


def make_harvest(**overrides):
    """Create a posted harvest, defaulting to a gram yield from a new batch."""
    values = {
        'quantity': Decimal('1'),
        'unit_code': UnitCode.GRAM,
        'notes': 'Shared test harvest',
    }
    if 'batch' not in overrides:
        values['batch'] = make_production_batch()
    if 'harvested_at' not in overrides:
        values['harvested_at'] = timezone.now()
    values.update(overrides)
    return Harvest.objects.create(**values)


def make_harvest_plant(**overrides):
    """Attribute a harvest to one plant, building either side when absent."""
    values = dict(overrides)
    plant = values.pop('plant', None) or make_specific_plant()
    defaults = {
        'plant': plant,
        'harvest': make_harvest(
            batch=plant.cell_planting.seed_tray_planting.batch,
            workspace=plant.workspace,
        ),
    }
    defaults.update(values)
    return HarvestPlant.objects.create(**defaults)


def make_specific_plant_location(**overrides):
    """Create a valid seed-tray location for a specific plant."""
    values = dict(overrides)
    plant = values.pop('specific_plant', None) or make_specific_plant()
    defaults = {
        'specific_plant': plant,
        'location_type': SpecificPlantLocation.SEED_TRAY_CELL,
        'seed_tray_cell': plant.cell_planting.cell,
        'notes': 'Shared test location',
    }
    defaults.update(values)
    return SpecificPlantLocation.objects.create(**defaults)
