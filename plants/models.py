"""
Models for plants
"""
from django.db import models

from common.merging import MergeableModel
from workspaces.models import WorkspaceOwnedModel


class MaturityBasis(models.TextChoices):
    """The cultivation event from which maturity days are counted."""

    SEED = 'seed', 'From seed'
    TRANSPLANTING = 'transplanting', 'From transplanting'


class PlantFamily(MergeableModel, WorkspaceOwnedModel):
    """
    Plant Family
    """
    name = models.CharField(max_length=1024)
    notes = models.TextField(null=True, blank=True)

    retirement_dependants = (('plant_set', 'plants'),)

    def search_names(self):
        """Return the names this family is found by.

        Everything filed under it counts, so that a search for one variety
        keeps the family it hangs off and the screen still reads as a tree.
        """
        yield self.name
        for plant in self.plant_set.all():
            yield plant.name
            for variety in plant.plantvariety_set.all():
                yield variety.name

    def __str__(self):
        return self.name


class Plant(MergeableModel, WorkspaceOwnedModel):
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

    retirement_parents = ('family',)
    retirement_dependants = (('plantvariety_set', 'varieties'),)

    def search_names(self):
        """Return the names this crop is found by.

        Its own, the family above it, and every variety filed under it.
        """
        yield self.name
        yield self.family.name
        for variety in self.plantvariety_set.all():
            yield variety.name

    def __str__(self):
        return self.name


class PlantVariety(MergeableModel, WorkspaceOwnedModel):
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

    retirement_parents = ('plant',)
    retirement_dependants = (('seeds_set', 'seed catalog entries'),)

    @property
    def effective_maturity_basis(self):
        """Return this variety's override or its plant's default."""
        return self.maturity_basis or self.plant.maturity_basis

    def search_names(self):
        """Return the names this variety is found by.

        Nothing hangs off a variety in the crop hierarchy, so this is the crop
        and the family above it and no more.
        """
        yield self.name
        yield self.plant.name
        yield self.plant.family.name

    def __str__(self):
        return self.name
