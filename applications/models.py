"""Auditable records of an input applied to a batch, cells, plants, or ground.

An inventory balance says how much is left but not where it went. These
documents close that gap: each one names the exact lot it drew from, the
quantity an operator confirmed, and the physical things it was applied to.

Every number the calculation depended on is copied onto the document when it
posts. A tray model's cell volume, an item's configured rate, and a garden
area's confirmed scale are all editable afterwards, so reading them live would
let a later edit silently rewrite what was already applied.
"""

# pylint: disable=duplicate-code

import operator
from decimal import Decimal
from functools import reduce

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from garden.models import GardenArea, GardenBed, GardenRow, GardenSquare
from inventory.models import (
    POSITIVE_DECIMAL,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    InventoryItem,
    InventoryLocation,
    InventoryUnit,
    ItemUnitConversion,
    StockLot,
    StockMovement,
)
from inventory.units import UnitCode
from plantings.models import ProductionBatch, SpecificPlant
from seedtrays.models import SeedTrayCell
from workspaces.models import WorkspaceOwnedModel


#: Precision for a calculation weight and a fill factor. Both are ratios rather
#: than quantities, so they do not need the ledger's nine decimal places.
FACTOR_MAX_DIGITS = 12
FACTOR_DECIMAL_PLACES = 6
POSITIVE_FACTOR = Decimal('0.000001')

#: Precision for a normalized surface area, matching `garden.geometry`.
AREA_MAX_DIGITS = 18
AREA_DECIMAL_PLACES = 6

#: Columns that can hold an application target. Each name is also the
#: ``target_type`` value selecting it, which lets the identity constraint be
#: generated rather than written out once per target. It is kept in step with
#: `InputApplicationTarget.TargetType` by a test.
TARGET_FIELDS = (
    'batch',
    'seed_tray_cell',
    'specific_plant',
    'inventory_unit',
    'garden_area',
    'garden_bed',
    'garden_row',
    'garden_square',
)


class InputApplication(WorkspaceOwnedModel):
    """One document recording inputs applied at a point in time.

    A draft is assembled, checked against stock, and then posted, which is when
    it decrements inventory. Posting is deliberately a separate step from
    creation: the calculation is a suggestion until an operator confirms what
    was actually used, and confirming can happen well after the draft is built.

    A posted document is never edited. A mistake is reversed, which restores
    the stock through linked reversal movements and leaves the original and its
    calculation on file; a correction is a new document.
    """

    class Status(models.TextChoices):
        """Input application lifecycle states."""

        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        REVERSED = 'reversed', 'Reversed'

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='input_applications',
    )
    applied_at = models.DateTimeField()
    source_location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name='input_applications',
    )
    notes = models.TextField(blank=True, default='')
    target_summary = models.TextField(blank=True, default='', editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    reverse_reason = models.TextField(blank=True, default='', editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-applied_at', '-pk']
        indexes = [
            models.Index(
                fields=['workspace', 'applied_at'],
                name='application_period_idx',
            ),
            models.Index(
                fields=['batch', 'applied_at'],
                name='application_batch_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(status='draft', posted_at__isnull=True, reversed_at__isnull=True),
                    models.Q(status='posted', posted_at__isnull=False, reversed_at__isnull=True),
                    models.Q(status='reversed', posted_at__isnull=False, reversed_at__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='application_status_stamp',
            ),
        ]

    def __str__(self):
        return f'Application {self.pk or "draft"} at {self.applied_at}'

    def clean(self):
        """Keep the batch and the drawing location inside this workspace."""
        super().clean()
        errors = {}
        if self.batch_id and self.batch.workspace_id != self.workspace_id:
            errors['batch'] = 'The batch belongs to a different workspace.'
        if self.source_location_id:
            if self.source_location.workspace_id != self.workspace_id:
                errors['source_location'] = (
                    'The location belongs to a different workspace.'
                )
            elif not self.source_location.active:
                errors['source_location'] = 'The location is inactive.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.pk and self.status != self.Status.DRAFT:
            raise ValidationError('Applications must be created as drafts.')
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Posted applications are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Only draft applications can be deleted.')
        return super().delete(*args, **kwargs)


class InputApplicationLine(models.Model):
    """One item drawn from one exact lot, with the calculation that suggested it.

    The calculated quantity is a suggestion; `applied_base_quantity` is the
    inventory fact. Both are kept so a report can show what was expected
    alongside what an operator actually used, and `override_reason` explains
    the gap whenever it is material.
    """

    application = models.ForeignKey(
        InputApplication,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='application_lines',
    )
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='application_lines',
    )
    usage_basis = models.CharField(
        max_length=16,
        choices=InventoryItem.UsageBasis.choices,
    )
    base_unit = models.CharField(max_length=16, choices=UnitCode.choices)
    configured_rate = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    configured_rate_unit = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        blank=True,
        default='',
    )
    configured_fixed_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    fill_factor = models.DecimalField(
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_FACTOR)],
    )
    formula_basis_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    formula_basis_unit = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        blank=True,
        default='',
    )
    calculated_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    applied_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    unit_code = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        null=True,
        blank=True,
    )
    unit_conversion = models.ForeignKey(
        ItemUnitConversion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_lines',
    )
    applied_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    waste_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    waste_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    waste_reason = models.TextField(blank=True, default='')
    override_reason = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    consumption_movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='application_consumption',
    )
    waste_movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='application_waste',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(applied_base_quantity__gt=0),
                name='application_line_positive_applied',
            ),
            models.CheckConstraint(
                condition=models.Q(waste_base_quantity__gte=0),
                name='application_line_nonnegative_waste',
            ),
            models.CheckConstraint(
                condition=(models.Q(fill_factor__isnull=True) | models.Q(fill_factor__gt=0)),
                name='application_line_positive_fill_factor',
            ),
            models.CheckConstraint(
                condition=(models.Q(configured_rate__isnull=True) | models.Q(configured_rate__gt=0)),
                name='application_line_positive_rate',
            ),
            models.CheckConstraint(
                condition=(models.Q(calculated_base_quantity__isnull=True) | models.Q(calculated_base_quantity__gte=0)),
                name='application_line_nonnegative_calculated',
            ),
        ]

    def __str__(self):
        return f'{self.applied_base_quantity} {self.base_unit} of {self.item}'

    def clean(self):
        """Check workspace, unit, and lot coherence for one line."""
        super().clean()
        errors = {}
        self._add_ownership_errors(errors)
        self._add_unit_errors(errors)
        if errors:
            raise ValidationError(errors)

    def _add_ownership_errors(self, errors):
        """Require the item, lot, and conversion to belong together."""
        if not self.application_id:
            return
        workspace_id = self.application.workspace_id
        if self.item_id and self.item.workspace_id != workspace_id:
            errors['item'] = 'The item belongs to a different workspace.'
        if self.lot_id:
            if self.lot.workspace_id != workspace_id:
                errors['lot'] = 'The lot belongs to a different workspace.'
            elif self.item_id and self.lot.item_id != self.item_id:
                errors['lot'] = 'The lot belongs to a different item.'

    def _add_unit_errors(self, errors):
        """Require exactly one display unit that belongs to this item."""
        if bool(self.unit_code) == bool(self.unit_conversion_id):
            errors['unit_code'] = (
                'Select exactly one controlled unit or item conversion.'
            )
        if self.unit_conversion_id and self.unit_conversion.item_id != self.item_id:
            errors['unit_conversion'] = 'The conversion does not belong to this item.'
        if self.item_id and self.base_unit and self.base_unit != self.item.base_unit:
            errors['base_unit'] = 'The snapshot does not match the item base unit.'

    def save(self, *args, **kwargs):
        if self.application_id:
            status = self.application.status
            if status != InputApplication.Status.DRAFT:
                raise ValidationError('Posted application lines are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.application.status != InputApplication.Status.DRAFT:
            raise ValidationError('Posted application lines are immutable.')
        return super().delete(*args, **kwargs)


class InputApplicationTarget(models.Model):
    """One physical thing a line was applied to, with its frozen measurement.

    Targets belong to the line rather than the document because the basis is
    per line: one application can spread media over 48 tray cells and put a
    label on each of 12 plants, and those are different sets of things.

    There is no generic foreign key here. Each supported target has its own
    column and a check constraint requires exactly one of them, which keeps the
    relationship enforceable by the database and protected against deletion.
    """

    class TargetType(models.TextChoices):
        """Things an input can be applied to.

        Each value is also the name of the column that holds it, which is what
        lets the identity constraint be generated rather than written out.
        """

        BATCH = 'batch', 'Production batch'
        SEED_TRAY_CELL = 'seed_tray_cell', 'Tray cell'
        SPECIFIC_PLANT = 'specific_plant', 'Plant'
        INVENTORY_UNIT = 'inventory_unit', 'Serialized unit'
        GARDEN_AREA = 'garden_area', 'Garden area'
        GARDEN_BED = 'garden_bed', 'Garden bed'
        GARDEN_ROW = 'garden_row', 'Garden row'
        GARDEN_SQUARE = 'garden_square', 'Garden square'

    line = models.ForeignKey(
        InputApplicationLine,
        on_delete=models.CASCADE,
        related_name='targets',
    )
    target_type = models.CharField(max_length=24, choices=TargetType.choices)
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    seed_tray_cell = models.ForeignKey(
        SeedTrayCell,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    specific_plant = models.ForeignKey(
        SpecificPlant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    inventory_unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    garden_area = models.ForeignKey(
        GardenArea,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    garden_bed = models.ForeignKey(
        GardenBed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    garden_row = models.ForeignKey(
        GardenRow,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    garden_square = models.ForeignKey(
        GardenSquare,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='application_targets',
    )
    weight = models.DecimalField(
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
        default=Decimal('1'),
        validators=[MinValueValidator(POSITIVE_FACTOR)],
        help_text='Share of this target that received the input.',
    )
    cell_volume_ml = models.PositiveIntegerField(null=True, blank=True)
    area_m2 = models.DecimalField(
        max_digits=AREA_MAX_DIGITS,
        decimal_places=AREA_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=255, blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        indexes = [
            models.Index(
                fields=['line', 'target_type'],
                name='application_target_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=reduce(
                    operator.or_,
                    (
                        models.Q(
                            target_type=chosen,
                            **{
                                f'{field}__isnull': field != chosen
                                for field in TARGET_FIELDS
                            },
                        )
                        for chosen in TARGET_FIELDS
                    ),
                ),
                name='application_target_identity',
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gt=0),
                name='application_target_positive_weight',
            ),
            models.CheckConstraint(
                condition=(models.Q(cell_volume_ml__isnull=True) | models.Q(cell_volume_ml__gt=0)),
                name='application_target_positive_cell_volume',
            ),
            models.CheckConstraint(
                condition=(models.Q(area_m2__isnull=True) | models.Q(area_m2__gt=0)),
                name='application_target_positive_area',
            ),
        ] + [
            models.UniqueConstraint(
                fields=['line', field],
                condition=models.Q(**{f'{field}__isnull': False}),
                name=f'application_target_unique_{field}',
            )
            for field in TARGET_FIELDS
        ]

    def __str__(self):
        return self.label or f'{self.get_target_type_display()} {self.target_id}'

    @property
    def target(self):
        """Return the one thing this row points at."""
        return getattr(self, self.target_type, None)

    @property
    def target_id(self):
        """Return the primary key of the one thing this row points at."""
        return getattr(self, f'{self.target_type}_id', None)

    def clean(self):
        """Require one in-workspace target matching the declared type."""
        super().clean()
        errors = {}
        populated = [
            field for field in TARGET_FIELDS
            if getattr(self, f'{field}_id', None) is not None
        ]
        if len(populated) != 1:
            errors['target_type'] = 'Select exactly one application target.'
        elif populated[0] != self.target_type:
            errors['target_type'] = 'The target does not match the declared type.'
        else:
            self._add_workspace_error(errors)
        if errors:
            raise ValidationError(errors)

    def _add_workspace_error(self, errors):
        """Require the target to sit in the document's workspace.

        A tray cell is reached through its tray because cells are not workspace
        owned in their own right.
        """
        if not self.line_id:
            return
        workspace_id = self.line.application.workspace_id
        target = self.target
        owner = target.tray if isinstance(target, SeedTrayCell) else target
        if owner.workspace_id != workspace_id:
            errors['target_type'] = 'The target belongs to a different workspace.'

    def save(self, *args, **kwargs):
        if self.line_id:
            status = self.line.application.status
            if status != InputApplication.Status.DRAFT:
                raise ValidationError('Posted application targets are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.line.application.status != InputApplication.Status.DRAFT:
            raise ValidationError('Posted application targets are immutable.')
        return super().delete(*args, **kwargs)
