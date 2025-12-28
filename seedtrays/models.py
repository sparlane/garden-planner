"""
Models for seed trays
"""
from django.db import models


class SeedTrayModel(models.Model):
    """
    A seed tray model used for starting seeds
    """
    identifier = models.CharField(max_length=256, unique=True)
    description = models.TextField(null=True, blank=True)

    # Dimensions of the tray itself
    height = models.PositiveIntegerField()
    x_size = models.PositiveIntegerField()
    y_size = models.PositiveIntegerField()

    x_cells = models.PositiveIntegerField()
    y_cells = models.PositiveIntegerField()
    cell_size_ml = models.PositiveIntegerField(help_text='Volume of each cell in milliliters')

    def __str__(self):
        return self.identifier


class SeedTray(models.Model):
    """
    A specific seed tray
    """
    model = models.ForeignKey(SeedTrayModel, on_delete=models.PROTECT)
    created = models.DateField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'Tray {self.model.identifier} created {self.created}'


class SeedTrayCell(models.Model):
    """
    A specific cell in a seed tray
    """
    tray = models.ForeignKey(SeedTray, on_delete=models.CASCADE)
    x_position = models.PositiveIntegerField()
    y_position = models.PositiveIntegerField()

    def __str__(self):
        return f'Cell ({self.x_position}, {self.y_position}) in Tray {self.tray.model.identifier}'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tray', 'x_position', 'y_position'], name='unique_cell_per_tray')
        ]
