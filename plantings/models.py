"""
Models for Plantings
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from seeds.models import SeedPacket
from seedtrays.models import SeedTray, SeedTrayCell
from garden.models import GardenRow, GardenSquare


class Planting(models.Model):
    """
    An abstract class for planting of seeds
    """
    planted = models.DateTimeField(default=timezone.now)
    seeds_used = models.ForeignKey(SeedPacket, on_delete=models.PROTECT)
    quantity = models.IntegerField()
    location = None
    notes = models.TextField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.quantity} {self.seeds_used.seeds.plant_variety} planted {self.planted} in {self.location}'

    class Meta:
        abstract = True


class GardenRowDirectSowPlanting(Planting):
    """
    Planting via direct sow into a garden row
    """
    location = models.ForeignKey(GardenRow, on_delete=models.PROTECT)


class GardenSquareDirectSowPlanting(Planting):
    """
    Planting via direct sow into a garden square
    """
    location = models.ForeignKey(GardenSquare, on_delete=models.PROTECT)


class SeedTrayPlanting(Planting):
    """
    Planting into a seed tray
    """
    location = models.CharField(max_length=1024, null=True, blank=True)
    seed_tray = models.ForeignKey(SeedTray, on_delete=models.PROTECT, null=True, blank=True)


class SeedTrayCellPlanting(models.Model):
    """
    Represents the number of seeds placed into a specific cell of a seed tray
    as part of a `SeedTrayPlanting` event. This lets us group an overall
    planting event while tracking per-cell quantities.
    """
    seed_tray_planting = models.ForeignKey(SeedTrayPlanting, on_delete=models.CASCADE, related_name='cell_plantings')
    cell = models.ForeignKey(SeedTrayCell, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seed_tray_planting', 'cell'], name='unique_cell_per_planting')
        ]

    def __str__(self):
        return f'{self.quantity} in {self.cell} for planting {self.seed_tray_planting.pk}'


class SpecificPlant(models.Model):
    """
    A specific individual plant that has germinated from a seed tray cell.
    Created when germination is observed for a particular cell planting.
    """
    cell_planting = models.ForeignKey(SeedTrayCellPlanting, on_delete=models.PROTECT, related_name='specific_plants')
    germinated = models.DateTimeField(default=timezone.now)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'Plant from {self.cell_planting} germinated {self.germinated}'


class SpecificPlantLocation(models.Model):
    """
    Tracks where a specific plant has been — a seed tray cell or garden square —
    and when it entered/left that location.
    """
    SEED_TRAY_CELL = 'seed_tray_cell'
    GARDEN_SQUARE = 'garden_square'
    LOCATION_TYPE_CHOICES = [
        (SEED_TRAY_CELL, 'Seed Tray Cell'),
        (GARDEN_SQUARE, 'Garden Square'),
    ]

    specific_plant = models.ForeignKey(SpecificPlant, on_delete=models.CASCADE, related_name='locations')
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES)
    seed_tray_cell = models.ForeignKey(SeedTrayCell, on_delete=models.PROTECT, null=True, blank=True)
    garden_square = models.ForeignKey(GardenSquare, on_delete=models.PROTECT, null=True, blank=True)
    started = models.DateTimeField(default=timezone.now)
    ended = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    def clean(self):
        if self.location_type == self.SEED_TRAY_CELL:
            if self.seed_tray_cell is None:
                raise ValidationError({'seed_tray_cell': 'Required when location_type is seed_tray_cell.'})
            if self.garden_square is not None:
                raise ValidationError({'garden_square': 'Must be blank when location_type is seed_tray_cell.'})
        elif self.location_type == self.GARDEN_SQUARE:
            if self.garden_square is None:
                raise ValidationError({'garden_square': 'Required when location_type is garden_square.'})
            if self.seed_tray_cell is not None:
                raise ValidationError({'seed_tray_cell': 'Must be blank when location_type is garden_square.'})

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['specific_plant'],
                condition=models.Q(ended__isnull=True),
                name='unique_active_location_per_plant',
            )
        ]

    def __str__(self):
        loc = self.seed_tray_cell or self.garden_square
        return f'Plant {self.specific_plant_id} @ {loc} from {self.started}'


class GardenSquareTransplant(models.Model):
    """
    Transplant from a seedtray into a garden square
    """
    original_planting = models.ForeignKey(SeedTrayPlanting, on_delete=models.PROTECT)
    transplanted = models.DateTimeField(default=timezone.now)
    quantity = models.IntegerField()
    location = models.ForeignKey(GardenSquare, on_delete=models.PROTECT)
    notes = models.TextField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.quantity} {self.original_planting.seeds_used.seeds.plant_variety} planted {self.original_planting.planted} transplanted {self.transplanted} in {self.location}'
