"""Small dependency-free model builders shared by API tests."""
from itertools import count

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from plantings.models import (
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from plants.models import Plant, PlantFamily, PlantVariety
from seeds.models import SeedPacket, Seeds
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel
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


def make_garden_area(**overrides):
    """Create a garden area with drawable dimensions."""
    values = {
        'name': _next_name('Area'),
        'size_x': 100,
        'size_y': 100,
    }
    values.update(overrides)
    return GardenArea.objects.create(**values)


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
        values['area'] = make_garden_area()
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
        values['bed'] = make_garden_bed()
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
        values['bed'] = make_garden_bed()
    values.update(overrides)
    return GardenSquare.objects.create(**values)


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
    """Create a seed tray without implicitly creating cells."""
    values = {
        'notes': 'Shared test tray',
    }
    if 'model' not in overrides:
        values['model'] = make_seed_tray_model()
    values.update(overrides)
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


def make_seed_tray_planting(**overrides):
    """Create a seed-tray planting and its related seed and tray graph."""
    values = {
        'quantity': 2,
        'notes': 'Shared test tray planting',
    }
    if 'seeds_used' not in overrides:
        values['seeds_used'] = make_seed_packet()
    if 'seed_tray' not in overrides:
        values['seed_tray'] = make_seed_tray()
    values.update(overrides)
    return SeedTrayPlanting.objects.create(**values)


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
