"""
Models for seed trays
"""
# pylint: disable=duplicate-code
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

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


class SeedTrayModel(WorkspaceOwnedModel):
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

    def __str__(self):
        return self.identifier

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
                has_history = previous.seedtray_set.exists() or previous.inventory_item.stock_history_started_at
                if has_history:
                    raise ValidationError({
                        'inventory_item': 'Cannot change the inventory item after tray or stock history exists.',
                    })
        if not self.inventory_item_id:
            self.inventory_item = InventoryItem.objects.create(
                workspace=self.workspace,
                name=f'Tray model: {self.identifier}',
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
    """One fill of one physical tray, and the cultivation cycle it carries.

    A cell says where something is, not which crop is using it. Emptying a tray
    and sowing it again reuses the same cells, so without a generation the media
    applied to the first crop cannot be told apart from the media applied to the
    second, and a whole-tray fill reads as though it belongs to every plant ever
    raised in those cells.

    A generation owns the media applied to its cells and the sowings made into
    them. It is closed by an explicit clean, never deleted, and reusing the tray
    opens the next one.
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
    )
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
                condition=models.Q(sequence__gte=1),
                name='tray_generation_sequence_gte_1',
            ),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        """Require a usable code and a tray inside this workspace."""
        super().clean()
        errors = {}
        if not self.code.strip():
            errors['code'] = 'A generation code is required.'
        if self.tray_id and self.tray.workspace_id != self.workspace_id:
            errors['tray'] = 'The tray belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('tray_id', 'sequence').first()
            errors = {}
            if previous and previous.tray_id != self.tray_id:
                errors['tray'] = 'Cannot move a generation to another tray.'
            if previous and previous.sequence != self.sequence:
                errors['sequence'] = 'Cannot renumber an existing generation.'
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
