from django.db import models


class PlantFamily(models.Model):
    name = models.CharField(max_length=1024)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.name


class Plant(models.Model):
    family = models.ForeignKey(PlantFamily, on_delete=models.PROTECT)
    name = models.CharField(max_length=1024)
    notes = models.TextField(null=True, blank=True)
    spacing = models.IntegerField(null=True, blank=True)
    inter_row_spacing = models.IntegerField(null=True, blank=True)
    plants_per_square_foot = models.IntegerField(null=True, blank=True)
    germination_days_min = models.IntegerField(null=True, blank=True)
    germination_days_max = models.IntegerField(null=True, blank=True)
    maturity_days_min = models.IntegerField(null=True, blank=True)
    maturity_days_max = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.name


class PlantVariety(models.Model):
    plant = models.ForeignKey(Plant, on_delete=models.PROTECT)
    name = models.CharField(max_length=1024)
    notes = models.TextField(null=True, blank=True)
    spacing = models.IntegerField(null=True, blank=True)
    inter_row_spacing = models.IntegerField(null=True, blank=True)
    plants_per_square_foot = models.IntegerField(null=True, blank=True)
    germination_days_min = models.IntegerField(null=True, blank=True)
    germination_days_max = models.IntegerField(null=True, blank=True)
    maturity_days_min = models.IntegerField(null=True, blank=True)
    maturity_days_max = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.name