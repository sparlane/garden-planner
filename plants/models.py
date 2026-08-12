"""
Models for plants
"""
from django.db import models

from workspaces.models import WorkspaceOwnedModel


class MaturityBasis(models.TextChoices):
    """The cultivation event from which maturity days are counted."""

    SEED = 'seed', 'From seed'
    TRANSPLANTING = 'transplanting', 'From transplanting'


class PlantFamily(WorkspaceOwnedModel):
    """
    Plant Family
    """
    name = models.CharField(max_length=1024)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.name


class Plant(WorkspaceOwnedModel):
    """
    A Plant
    """
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
    maturity_basis = models.CharField(
        max_length=16,
        choices=MaturityBasis.choices,
        default=MaturityBasis.SEED,
    )

    def __str__(self):
        return self.name


class PlantVariety(WorkspaceOwnedModel):
    """
    A Specific Variety of a Plant
    """
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
    maturity_basis = models.CharField(
        max_length=16,
        choices=MaturityBasis.choices,
        null=True,
        blank=True,
        default=None,
        help_text='Leave blank to inherit the plant default.',
    )

    @property
    def effective_maturity_basis(self):
        """Return this variety's override or its plant's default."""
        return self.maturity_basis or self.plant.maturity_basis

    def __str__(self):
        return self.name
