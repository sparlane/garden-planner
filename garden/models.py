"""
Garden models
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from workspaces.models import WorkspaceOwnedModel

from .layout import placement_errors


#: Smallest grid step a confirmation can record, matching ``cell_length``'s
#: six decimal places.
POSITIVE_LENGTH = Decimal('0.000001')


class GardenArea(WorkspaceOwnedModel):
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


class GardenGeometryConfirmation(WorkspaceOwnedModel):
    """One operator's audited statement of what an area's integers mean.

    Garden geometry is a bare integer grid: an area, bed, row, and square all
    carry ``size_x``/``size_y`` with no recorded physical scale. Nothing may
    read those integers as a length until somebody says what one grid step
    measures, so this record exists and areas without one stay unconfirmed.

    Confirmations are append-only and the newest one wins. A mistaken unit is
    corrected by confirming again, which leaves the original statement on file;
    input applications freeze the square metres they calculated, so a later
    correction never rewrites what was already applied.
    """

    class LengthUnit(models.TextChoices):
        """Physical units a grid step may be measured in."""

        MILLIMETRE = 'mm', 'Millimetres'
        CENTIMETRE = 'cm', 'Centimetres'
        METRE = 'm', 'Metres'
        INCH = 'in', 'Inches'
        FOOT = 'ft', 'Feet'

    area = models.ForeignKey(
        GardenArea,
        on_delete=models.PROTECT,
        related_name='geometry_confirmations',
    )
    length_unit = models.CharField(max_length=8, choices=LengthUnit.choices)
    cell_length = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        validators=[MinValueValidator(POSITIVE_LENGTH)],
        help_text='Physical length of one grid step, in the chosen unit.',
    )
    notes = models.TextField(blank=True, default='')
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    confirmed_at = models.DateTimeField(default=timezone.now, editable=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-confirmed_at', '-pk']
        indexes = [
            models.Index(
                fields=['area', '-confirmed_at'],
                name='garden_geometry_latest_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cell_length__gt=0),
                name='garden_geometry_positive_cell_length',
            ),
        ]

    def __str__(self):
        return f'{self.area}: 1 step = {self.cell_length} {self.length_unit}'

    def clean(self):
        """Keep a confirmation and the area it describes in one workspace."""
        super().clean()
        if self.area_id and self.area.workspace_id != self.workspace_id:
            raise ValidationError(
                {'area': 'The area belongs to a different workspace.'},
            )

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                'Geometry confirmations are immutable; confirm again instead.',
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Geometry confirmations cannot be deleted.')


class GardenBed(WorkspaceOwnedModel):
    """A rectangle of growing ground placed on an area's grid.

    ``kind`` records what the gardener said they were making rather than
    leaving a later screen to guess it from the name. It changes nothing about
    how the rectangle is measured or validated: a raised bed and a patch of
    open ground occupy their area the same way.
    """

    class Kind(models.TextChoices):
        """What a bed physically is, as the gardener described it."""

        IN_GROUND = 'in_ground', 'In-ground bed'
        RAISED = 'raised', 'Raised bed'
        CONTAINER = 'container', 'Container or planter'

    area = models.ForeignKey(GardenArea, on_delete=models.PROTECT)
    name = models.TextField(max_length=1024)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.IN_GROUND)
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

    def clean(self):
        """Keep this bed inside its area, off its neighbours, and in one workspace."""
        super().clean()
        if not self.area_id:
            return
        parent = self.area
        if parent.workspace_id != self.workspace_id:
            raise ValidationError(
                {'area': 'The area belongs to a different workspace.'},
            )
        siblings = GardenBed.objects.filter(area_id=self.area_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        errors = placement_errors(
            self,
            parent,
            siblings.only('name', 'placement_x', 'placement_y', 'size_x', 'size_y'),
            'The bed',
            f'area "{parent.name}"',
        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # The check constraints restate the field validators that
        # ``clean_fields`` has already run, and the database enforces them on
        # the insert regardless. Skipping them keeps a template that writes a
        # few hundred squares to one round trip per row.
        self.full_clean(validate_constraints=False)
        super().save(*args, **kwargs)


class GardenRow(WorkspaceOwnedModel):
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

    def clean(self):
        """Keep this row inside its bed, off its neighbours, and in one workspace."""
        super().clean()
        if not self.bed_id:
            return
        parent = self.bed
        if parent.workspace_id != self.workspace_id:
            raise ValidationError(
                {'bed': 'The bed belongs to a different workspace.'},
            )
        siblings = GardenRow.objects.filter(bed_id=self.bed_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        errors = placement_errors(
            self,
            parent,
            siblings.only('name', 'placement_x', 'placement_y', 'size_x', 'size_y'),
            'The row',
            f'bed "{parent.name}"',
        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # The check constraints restate the field validators that
        # ``clean_fields`` has already run, and the database enforces them on
        # the insert regardless. Skipping them keeps a template that writes a
        # few hundred squares to one round trip per row.
        self.full_clean(validate_constraints=False)
        super().save(*args, **kwargs)


class GardenSquare(WorkspaceOwnedModel):
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

    def clean(self):
        """Keep this square inside its bed, off its neighbours, and in one workspace."""
        super().clean()
        if not self.bed_id:
            return
        parent = self.bed
        if parent.workspace_id != self.workspace_id:
            raise ValidationError(
                {'bed': 'The bed belongs to a different workspace.'},
            )
        siblings = GardenSquare.objects.filter(bed_id=self.bed_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        errors = placement_errors(
            self,
            parent,
            siblings.only('name', 'placement_x', 'placement_y', 'size_x', 'size_y'),
            'The square',
            f'bed "{parent.name}"',
        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # The check constraints restate the field validators that
        # ``clean_fields`` has already run, and the database enforces them on
        # the insert regardless. Skipping them keeps a template that writes a
        # few hundred squares to one round trip per row.
        self.full_clean(validate_constraints=False)
        super().save(*args, **kwargs)

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
