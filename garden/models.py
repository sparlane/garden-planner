"""
Garden models
"""

from django.core.validators import MinValueValidator
from django.db import models


class GardenArea(models.Model):
    """
    An Area of garden
    """
    name = models.TextField(max_length=1024)
    size_x = models.IntegerField(validators=[MinValueValidator(1)])
    size_y = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_x__gte=1, size_y__gte=1),
                name='area_size_gte_1',
            ),
        ]

    def __str__(self):
        return self.name


class GardenBed(models.Model):
    """
    A Garden bed
    """
    area = models.ForeignKey(GardenArea, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField(validators=[MinValueValidator(0)])
    placement_y = models.IntegerField(validators=[MinValueValidator(0)])
    size_x = models.IntegerField(validators=[MinValueValidator(1)])
    size_y = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_x__gte=1, size_y__gte=1),
                name='bed_size_gte_1',
            ),
            models.CheckConstraint(
                condition=models.Q(placement_x__gte=0, placement_y__gte=0),
                name='bed_placement_gte_0',
            ),
        ]

    def __str__(self):
        return self.name


class GardenRow(models.Model):
    """
    A Row in a garden bed
    """
    bed = models.ForeignKey(GardenBed, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField(validators=[MinValueValidator(0)])
    placement_y = models.IntegerField(validators=[MinValueValidator(0)])
    size_x = models.IntegerField(validators=[MinValueValidator(1)])
    size_y = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_x__gte=1, size_y__gte=1),
                name='row_size_gte_1',
            ),
            models.CheckConstraint(
                condition=models.Q(placement_x__gte=0, placement_y__gte=0),
                name='row_placement_gte_0',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.size_x},{self.size_y}) @ ({self.placement_x},{self.placement_y}) in {self.bed}'


class GardenSquare(models.Model):
    """
    A square in a garden bed
    """
    bed = models.ForeignKey(GardenBed, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    placement_x = models.IntegerField(validators=[MinValueValidator(0)])
    placement_y = models.IntegerField(validators=[MinValueValidator(0)])
    size_x = models.IntegerField(validators=[MinValueValidator(1)])
    size_y = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_x__gte=1, size_y__gte=1),
                name='square_size_gte_1',
            ),
            models.CheckConstraint(
                condition=models.Q(placement_x__gte=0, placement_y__gte=0),
                name='square_placement_gte_0',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.size_x},{self.size_y}) @ ({self.placement_x},{self.placement_y}) in {self.bed}'

    def as_json(self):
        """
        Return an object that can be used as json
        """
        return {
            'pk': self.pk,
            'bed': self.bed.name,
            'area': self.bed.area.name,
            'name': self.name,
            'placement_x': self.placement_x,
            'placement_y': self.placement_y,
            'size_x': self.size_x,
            'size_y': self.size_y
        }
