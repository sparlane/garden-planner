"""
Models for seed trays
"""
# pylint: disable=duplicate-code
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from common.replacement import ReplaceableModel
from inventory.models import (
    COST_DECIMAL_PLACES,
    COST_MAX_DIGITS,
    POSITIVE_DECIMAL,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    InventoryItem,
    InventoryUnit,
    StockLot,
    StockMovement,
)
from inventory.units import UnitCode
from workspaces.models import WorkspaceOwnedModel


class SeedTrayModel(ReplaceableModel, WorkspaceOwnedModel):
    """
    A seed tray model used for starting seeds
    """
    identifier = models.CharField(max_length=256)
    inventory_item = models.OneToOneField(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='seed_tray_model',
    )
    description = models.TextField(null=True, blank=True)

    # Dimensions of the tray itself
    height = models.PositiveIntegerField()
    x_size = models.PositiveIntegerField()
    y_size = models.PositiveIntegerField()

    x_cells = models.PositiveIntegerField()
    y_cells = models.PositiveIntegerField()
    cell_size_ml = models.PositiveIntegerField(help_text='Volume of each cell in milliliters')

    #: What a tray of this model *is*. The grid is the one part of the spec
    #: that has already been written down elsewhere: every tray received
    #: against the model has a cell at each of those coordinates, and sowings,
    #: plants and media applications name those cells. The outer measurements
    #: and the cell volume describe the same trays rather than materialize
    #: anything, and what a posted document worked out from a cell volume was
    #: frozen when it was posted, so correcting one of those stays a
    #: correction.
    identity_fields = ('x_cells', 'y_cells')

    def search_names(self):
        """Return the names this model is found by.

        Its identifier and no more: the description beside it is prose about a
        tray rather than another way of naming one.
        """
        yield self.identifier

    def __str__(self):
        return self.identifier

    @property
    def inventory_item_name(self):
        """Return what the paired stock identity is called after this model."""
        return f'Tray model: {self.identifier}'

    def has_history(self):
        """Say whether physical trays or posted stock hang off this model."""
        if self.pk is None:
            return False
        if self.seedtray_set.exists():
            return True
        return bool(
            self.inventory_item_id and self.inventory_item.stock_history_started_at
        )

    def identity_locked(self):
        """Say whether received trays have frozen the grid this model names.

        A tray is received as a physical object with cells at the coordinates
        this grid gave it, and every sowing, plant and application names one of
        them. Re-cutting the grid afterwards would say those trays were always
        something else, so the model is replaced instead and the trays already
        on the shelf keep the one they were built to.
        """
        return self.has_history()

    def clean(self):
        """Require a compatible serialized tray catalog identity."""
        super().clean()
        if not self.inventory_item_id:
            return
        errors = {}
        if self.inventory_item.workspace_id != self.workspace_id:
            errors['inventory_item'] = 'The inventory item belongs to a different workspace.'
        if self.inventory_item.category != InventoryItem.Category.TRAY:
            errors['inventory_item'] = 'Seed tray models require a tray-category item.'
        if self.inventory_item.tracking_mode != InventoryItem.TrackingMode.SERIALIZED:
            errors['inventory_item'] = 'Seed tray models require a serialized item.'
        if self.inventory_item.base_unit != 'each':
            errors['inventory_item'] = 'Seed tray items must use each as their base unit.'
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def save(self, *args, **kwargs):
        """Create the default catalog mapping and lock used relationships."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.inventory_item_id != self.inventory_item_id:
                if previous.has_history():
                    raise ValidationError({
                        'inventory_item': 'Cannot change the inventory item after tray or stock history exists.',
                    })
        if not self.inventory_item_id:
            self.inventory_item = InventoryItem.objects.create(
                workspace=self.workspace,
                name=self.inventory_item_name,
                category=InventoryItem.Category.TRAY,
                base_unit='each',
                tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
                default_usage_basis=InventoryItem.UsageBasis.MANUAL,
            )
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'identifier'],
                name='unique_tray_model_identifier_workspace',
            ),
        ]


class SeedTray(WorkspaceOwnedModel):
    """
    A specific seed tray
    """
    model = models.ForeignKey(SeedTrayModel, on_delete=models.PROTECT)
    inventory_unit = models.OneToOneField(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name='seed_tray',
    )
    created = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'Tray {self.model.identifier} created {self.created}'

    def clean(self):
        """Keep a physical tray aligned with its model and unit identity."""
        super().clean()
        errors = {}
        if self.model_id and self.model.workspace_id != self.workspace_id:
            errors['model'] = 'The tray model belongs to a different workspace.'
        if self.inventory_unit_id:
            if self.inventory_unit.workspace_id != self.workspace_id:
                errors['inventory_unit'] = 'The inventory unit belongs to a different workspace.'
            if self.model_id and self.inventory_unit.item_id != self.model.inventory_item_id:
                errors['inventory_unit'] = 'The inventory unit does not match the tray model.'
        else:
            errors['inventory_unit'] = 'Create trays through an audited inventory workflow.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate identity and prevent relationship changes after creation."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            errors = {}
            if previous and previous.model_id != self.model_id:
                errors['model'] = 'Cannot change the model of an existing tray.'
            if previous and previous.inventory_unit_id != self.inventory_unit_id:
                errors['inventory_unit'] = 'Cannot change the unit of an existing tray.'
            if errors:
                raise ValidationError(errors)
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)


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


class SeedTrayGeneration(WorkspaceOwnedModel):
    """One fill of an identified container or a counted lot at a place.

    The original model name and tray relationship remain for existing callers.
    Media, lifecycle events and residuals belong to this same record for every
    container. The count is fixed when filled; later exits must not change the
    basis on which plants share its media.
    """

    class Status(models.TextChoices):
        """Whether this fill is still in use."""

        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    class Origin(models.TextChoices):
        """How this generation came to exist."""

        OPERATOR = 'operator', 'Opened by an operator'
        LEGACY = 'legacy', 'Migrated from existing sowings'

    class ReviewState(models.TextChoices):
        """Whether migrated data needs an operator decision."""

        NONE = 'none', 'None'
        NEEDS_REVIEW = 'needs_review', 'Needs review'

    tray = models.ForeignKey(
        SeedTray,
        on_delete=models.PROTECT,
        related_name='generations',
        null=True,
        blank=True,
    )
    # The shared container identity; tray remains the compatibility relationship
    # while the fill workflows are generalized to numbered and counted pots.
    inventory_unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name='container_fills',
        null=True,
        blank=True,
        editable=False,
    )
    stock_lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name='container_fills',
        null=True, blank=True,
    )
    source_location = models.ForeignKey(
        'locations.Location', on_delete=models.PROTECT, related_name='container_fills',
        null=True, blank=True,
    )
    container_count = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    code = models.CharField(max_length=64)
    sequence = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        editable=False,
    )
    origin = models.CharField(
        max_length=16,
        choices=Origin.choices,
        default=Origin.OPERATOR,
        editable=False,
    )
    review_state = models.CharField(
        max_length=16,
        choices=ReviewState.choices,
        default=ReviewState.NONE,
        editable=False,
    )
    review_details = models.TextField(blank=True, default='', editable=False)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    close_reason = models.TextField(blank=True, default='', editable=False)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tray', '-sequence', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='tray_generation_workspace_code_unique',
            ),
            models.UniqueConstraint(
                fields=['tray', 'sequence'],
                name='tray_generation_tray_sequence_unique',
            ),
            # One open generation per tray. Because a cell belongs to exactly one
            # tray, this is also what stops a cell being allocated to two
            # simultaneously open generations, without a second rule to keep in
            # step with this one.
            models.UniqueConstraint(
                fields=['tray'],
                condition=models.Q(status='open'),
                name='tray_generation_single_open',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(status='open', closed_at__isnull=True),
                    models.Q(status='closed', closed_at__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='tray_generation_close_stamp',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(inventory_unit__isnull=False, stock_lot__isnull=True,
                             source_location__isnull=True, container_count=1),
                    models.Q(inventory_unit__isnull=True, tray__isnull=True,
                             stock_lot__isnull=False, source_location__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='container_fill_exactly_one_target',
            ),
            models.CheckConstraint(
                condition=models.Q(container_count__gte=1),
                name='container_fill_positive_count',
            ),
            models.UniqueConstraint(
                fields=['inventory_unit', 'sequence'],
                name='container_fill_unit_sequence_unique',
            ),
            models.UniqueConstraint(
                fields=['inventory_unit'], condition=models.Q(status='open'),
                name='container_fill_single_open_unit',
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1),
                name='tray_generation_sequence_gte_1',
            ),
        ]

    def __str__(self):
        return self.code

    def clean_fields(self, exclude=None):
        """Reject fractional counts before IntegerField can truncate them."""
        if 'container_count' not in (exclude or ()):
            try:
                count = Decimal(str(self.container_count))
                if not count.is_finite() or count != count.to_integral_value():
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise ValidationError({'container_count': 'Container counts must be whole numbers.'}) from exc
        super().clean_fields(exclude=exclude)

    def clean(self):
        """Keep the fill's physical target and count inside one workspace."""
        super().clean()
        errors = {}
        if not self.code.strip():
            errors['code'] = 'A generation code is required.'
        for field in ('tray', 'inventory_unit', 'stock_lot', 'source_location'):
            if getattr(self, f'{field}_id') and getattr(self, field).workspace_id != self.workspace_id:
                errors[field] = 'The fill target belongs to a different workspace.'
        self._validate_container_target(errors)
        if errors:
            raise ValidationError(errors)

    def _validate_container_target(self, errors):
        """Only actual trays and whole pots can carry a fill."""
        if self.inventory_unit_id:
            item = self.inventory_unit.item
            if self.tray_id and self.inventory_unit_id != self.tray.inventory_unit_id:
                errors['inventory_unit'] = 'The container does not match the tray.'
            if item.category == InventoryItem.Category.TRAY and not self.tray_id:
                errors['tray'] = 'A tray fill must retain its tray relationship.'
            if item.category not in (InventoryItem.Category.TRAY, InventoryItem.Category.POT_CONTAINER):
                errors['inventory_unit'] = 'Fills require a tray or pot container.'
            if item.base_unit != UnitCode.EACH:
                errors['inventory_unit'] = 'Containers must be counted in each.'
        if self.stock_lot_id:
            item = self.stock_lot.item
            if item.category != InventoryItem.Category.POT_CONTAINER or item.base_unit != UnitCode.EACH:
                errors['stock_lot'] = 'Counted fills require pot containers measured in each.'
            if item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
                errors['stock_lot'] = 'Serialized containers must name an inventory unit.'

    def save(self, *args, **kwargs):
        if not self.inventory_unit_id and self.tray_id:
            self.inventory_unit = self.tray.inventory_unit
        if self.pk:
            immutable = {
                'tray': ('tray_id', 'Cannot move a generation to another tray.'),
                'inventory_unit': ('inventory_unit_id', 'Cannot move a fill to another container.'),
                'stock_lot': ('stock_lot_id', 'Cannot change the lot a fill was opened for.'),
                'source_location': ('source_location_id', "Cannot change a fill's original location."),
                'container_count': ('container_count', "Cannot change a fill's share basis."),
                'sequence': ('sequence', 'Cannot renumber an existing generation.'),
            }
            previous = type(self).objects.filter(pk=self.pk).only(
                *(attribute for attribute, _ in immutable.values()),
            ).first()
            errors = {
                field: message for field, (attribute, message) in immutable.items()
                if previous and getattr(previous, attribute) != getattr(self, attribute)
            }
            if errors:
                raise ValidationError(errors)
        self.full_clean()
        super().save(*args, **kwargs)


class SeedTrayGenerationEvent(models.Model):
    """One immutable record of a generation lifecycle change.

    Reopening a mistaken clean appends a fact here rather than editing the
    closed generation, so the close, its time, and its stated reason stay
    readable next to the correction that undid them.
    """

    class EventType(models.TextChoices):
        """Recorded generation lifecycle facts."""

        OPENED = 'opened', 'Opened'
        CLOSED = 'closed', 'Closed'
        REOPENED = 'reopened', 'Reopened'
        REVIEWED = 'reviewed', 'Reviewed'

    generation = models.ForeignKey(
        SeedTrayGeneration,
        on_delete=models.PROTECT,
        related_name='events',
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    occurred_at = models.DateTimeField()
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_at', 'pk']

    def __str__(self):
        return f'Generation {self.generation_id}: {self.event_type} at {self.occurred_at}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Generation events are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Generation events cannot be deleted.')


class SeedTrayGenerationResidual(models.Model):
    """One disposition an operator recorded while cleaning a generation.

    Media and seed both left inventory when they were applied or sown, so
    throwing the remainder away moves no stock: that would decrement a lot the
    original consumption already decremented. A discarded remainder is recorded
    here and nowhere else, and task 43 reads these rows to move its cost to
    production loss. A remainder returned to stock is different — it physically
    comes back — so that one carries an ``adjustment_gain`` movement.

    ``unit_cost`` is copied from the lot for the same reason
    ``InputApplicationLine.configured_rate`` is: revaluing a lot afterwards must
    not silently rewrite what was already reported as loss.
    """

    class Kind(models.TextChoices):
        """What was left over."""

        MEDIA = 'media', 'Growing media'
        SEED = 'seed', 'Unsown seed'

    class Disposition(models.TextChoices):
        """What the operator did with it."""

        WASTE = 'waste', 'Discarded as waste'
        RECLAIMED = 'reclaimed', 'Reclaimed into stock'
        REMOVED = 'removed', 'Removed and not kept'
        RETURNED = 'returned', 'Returned to stock'

    #: The dispositions that put something physically back on the shelf, and so
    #: are the ones that post a movement.
    RECOVERING = ('reclaimed', 'returned')

    generation = models.ForeignKey(
        SeedTrayGeneration,
        on_delete=models.PROTECT,
        related_name='residuals',
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    disposition = models.CharField(max_length=16, choices=Disposition.choices)
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='tray_generation_residuals',
    )
    sowing = models.ForeignKey(
        'plantings.SeedTrayPlanting',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='generation_residuals',
    )
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    base_unit = models.CharField(max_length=16, choices=UnitCode.choices)
    unit_cost = models.DecimalField(
        max_digits=COST_MAX_DIGITS,
        decimal_places=COST_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='tray_generation_residual',
    )
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['generation', 'kind', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(base_quantity__gt=0),
                name='tray_generation_residual_positive_quantity',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(kind='media', disposition__in=['waste', 'reclaimed'], sowing__isnull=True),
                    models.Q(kind='seed', disposition__in=['removed', 'returned'], sowing__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='tray_generation_residual_kind_disposition',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(disposition__in=['reclaimed', 'returned']),
                    models.Q(disposition__in=['waste', 'removed'], movement__isnull=True),
                    _connector=models.Q.OR,
                ),
                name='tray_generation_residual_movement',
            ),
        ]

    def __str__(self):
        return (
            f'{self.base_quantity} {self.base_unit} of {self.lot} '
            f'{self.get_disposition_display().lower()}'
        )

    def clean(self):
        """Keep the lot, the sowing, and the movement in one workspace."""
        super().clean()
        errors = {}
        workspace_id = self.generation.workspace_id if self.generation_id else None
        if self.lot_id and workspace_id and self.lot.workspace_id != workspace_id:
            errors['lot'] = 'The lot belongs to a different workspace.'
        if self.lot_id and self.base_unit and self.base_unit != self.lot.item.base_unit:
            errors['base_unit'] = 'The snapshot does not match the item base unit.'
        if self.sowing_id and self.sowing.generation_id != self.generation_id:
            errors['sowing'] = 'The sowing belongs to a different generation.'
        if self.movement_id and workspace_id and self.movement.workspace_id != workspace_id:
            errors['movement'] = 'The movement belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Generation residuals are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Generation residuals cannot be deleted.')

    @property
    def reversed_movement(self):
        """Return the reversal of this residual's movement, when there is one."""
        if self.movement_id is None:
            return None
        return getattr(self.movement, 'reversal', None)
