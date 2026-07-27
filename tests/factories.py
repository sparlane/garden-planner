"""Small dependency-free model builders shared by API tests."""
from itertools import count

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
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
        'family': make_plant_family(),
        'name': _next_name('Plant'),
        'notes': 'Shared test plant',
    }
    values.update(overrides)
    return Plant.objects.create(**values)


def make_plant_variety(**overrides):
    """Create a plant variety with a plant when one is not supplied."""
    values = {
        'plant': make_plant(),
        'name': _next_name('Variety'),
        'notes': 'Shared test variety',
    }
    values.update(overrides)
    return PlantVariety.objects.create(**values)


def make_seeds(**overrides):
    """Create a seed product with its supplier and variety graph."""
    values = {
        'supplier': make_supplier(),
        'plant_variety': make_plant_variety(),
        'supplier_code': _next_name('CODE'),
        'url': 'https://supplier.example.com/seeds',
        'notes': 'Shared test seeds',
    }
    values.update(overrides)
    return Seeds.objects.create(**values)


def make_seed_packet(**overrides):
    """Create a seed packet and its seed product graph."""
    values = {
        'seeds': make_seeds(),
        'notes': 'Shared test packet',
    }
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
        'area': make_garden_area(),
        'name': _next_name('Bed'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 50,
        'size_y': 50,
    }
    values.update(overrides)
    return GardenBed.objects.create(**values)


def make_garden_row(**overrides):
    """Create a garden row and its parent geometry."""
    values = {
        'bed': make_garden_bed(),
        'name': _next_name('Row'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 10,
        'size_y': 1,
    }
    values.update(overrides)
    return GardenRow.objects.create(**values)


def make_garden_square(**overrides):
    """Create a garden square and its parent geometry."""
    values = {
        'bed': make_garden_bed(),
        'name': _next_name('Square'),
        'placement_x': 0,
        'placement_y': 0,
        'size_x': 1,
        'size_y': 1,
    }
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
        'model': make_seed_tray_model(),
        'notes': 'Shared test tray',
    }
    values.update(overrides)
    return SeedTray.objects.create(**values)


def make_seed_tray_cell(**overrides):
    """Create a seed-tray cell and its parent tray."""
    values = {
        'tray': make_seed_tray(),
        'x_position': 0,
        'y_position': 0,
    }
    values.update(overrides)
    return SeedTrayCell.objects.create(**values)
