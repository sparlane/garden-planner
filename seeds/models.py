"""
Models related to seeds
"""
from django.db import models

from plants.models import PlantVariety


class Supplier(models.Model):
    """
    A seed supplier
    """
    name = models.CharField(max_length=1024)
    website = models.CharField(max_length=1024, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Seeds(models.Model):
    """
    Seeds for a specific plant
    """
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    plant_variety = models.ForeignKey(PlantVariety, on_delete=models.PROTECT)
    supplier_code = models.CharField(max_length=32, blank=True, null=True)
    url = models.CharField(max_length=1024, blank=True, null=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.plant_variety} from {self.supplier} ({self.supplier_code})"


class SeedPacket(models.Model):
    """
    Specific packet/store of seeds
    """
    seeds = models.ForeignKey(Seeds, on_delete=models.PROTECT)
    purchase_date = models.DateField(null=True, blank=True)
    sow_by = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.seeds} sow by {self.sow_by}"
