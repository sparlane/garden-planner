"""
Models for Plantings
"""
from datetime import date

from django.db import models

from seeds.models import SeedPacket
from seedtrays.models import SeedTray, SeedTrayCell
from garden.models import GardenRow, GardenSquare


class Planting(models.Model):
    """
    An abstract class for planting of seeds
    """
    planted = models.DateField(default=date.today)
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


class GardenSquareTransplant(models.Model):
    """
    Transplant from a seedtray into a garden square
    """
    original_planting = models.ForeignKey(SeedTrayPlanting, on_delete=models.PROTECT)
    transplanted = models.DateField(default=date.today)
    quantity = models.IntegerField()
    location = models.ForeignKey(GardenSquare, on_delete=models.PROTECT)
    notes = models.TextField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.quantity} {self.original_planting.seeds_used.seeds.plant_variety} planted {self.original_planting.planted} transplanted {self.transplanted} in {self.location}'
