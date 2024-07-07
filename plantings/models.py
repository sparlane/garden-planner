from django.db import models
from django.utils import timezone

from seeds.models import SeedPacket
from garden.models import GardenRow, GardenSquare


class Planting(models.Model):
    """
    An abstract class for planting of seeds
    """
    planted = models.DateField(default=timezone.now)
    seeds_used = models.ForeignKey(SeedPacket, on_delete=models.PROTECT)
    quantity = models.IntegerField()
    location = None
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'{self.quantity} {self.seeds_used.seeds.variety} planted {self.planted} in {self.location}'

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
    location = models.CharField(max_length=1024)