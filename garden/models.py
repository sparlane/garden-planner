"""
Garden models
"""

from django.db import models


class GardenArea(models.Model):
    """
    An Area of garden
    """
    name = models.TextField(max_length=1024)
    size_x = models.IntegerField()
    size_y = models.IntegerField()

    def __str__(self):
        return self.name


class GardenBed(models.Model):
    """
    A Garden bed
    """
    area = models.ForeignKey(GardenArea, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField()
    placement_y = models.IntegerField()
    size_x = models.IntegerField()
    size_y = models.IntegerField()

    def __str__(self):
        return self.name


class GardenRow(models.Model):
    """
    A Row in a garden bed
    """
    bed = models.ForeignKey(GardenBed, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField()
    placement_y = models.IntegerField()
    size_x = models.IntegerField()
    size_y = models.IntegerField()


class GardenSquare(models.Model):
    """
    A square in a garden bed
    """
    bed = models.ForeignKey(GardenBed, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField()
    placement_y = models.IntegerField()
    size_x = models.IntegerField()
    size_y = models.IntegerField()
