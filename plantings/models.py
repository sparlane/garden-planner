"""
Models for Plantings
"""
# pylint: disable=duplicate-code
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from inventory.models import StockMovement
from plants.models import PlantVariety
from seeds.models import SeedPacket
from seedtrays.models import SeedTray, SeedTrayCell
from garden.models import GardenRow, GardenSquare
from workspaces.models import WorkspaceOwnedModel


class ProductionBatch(WorkspaceOwnedModel):
    """
    The shared cultivation identity for one tracked crop.

    A batch groups the sowings that intentionally produce the same crop so that
    lifecycle, input, and costing work attaches to one profile-neutral record
    instead of to tray-specific implementation details.
    """

    class Status(models.TextChoices):
        """Batch lifecycle states."""

        PLANNED = 'planned', 'Planned'
        ACTIVE = 'active', 'Active'
        OUTPUT_FINALIZED = 'output_finalized', 'Output finalized'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    class RepairState(models.TextChoices):
        """Whether migrated data needs an operator decision."""

        NONE = 'none', 'None'
        NEEDS_REPAIR = 'needs_repair', 'Needs repair'

    code = models.CharField(max_length=64)
    variety = models.ForeignKey(
        PlantVariety,
        on_delete=models.PROTECT,
        related_name='production_batches',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
        editable=False,
    )
    planned_start = models.DateField(null=True, blank=True)
    actual_start = models.DateTimeField(null=True, blank=True, editable=False)
    output_finalized_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    repair_state = models.CharField(
        max_length=16,
        choices=RepairState.choices,
        default=RepairState.NONE,
        editable=False,
    )
    repair_details = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='production_batch_workspace_code_unique',
            ),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        """Require a usable code and a variety inside this workspace."""
        super().clean()
        errors = {}
        if not self.code.strip():
            errors['code'] = 'A batch code is required.'
        if self.variety_id and self.variety.workspace_id != self.workspace_id:
            errors['variety'] = 'The variety belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductionBatchTransition(models.Model):
    """One immutable record of a batch lifecycle change."""

    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='transitions',
    )
    previous_status = models.CharField(
        max_length=20,
        choices=ProductionBatch.Status.choices,
        blank=True,
        default='',
    )
    new_status = models.CharField(
        max_length=20,
        choices=ProductionBatch.Status.choices,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    reason = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']

    def __str__(self):
        previous = self.previous_status or 'new'
        return f'Batch {self.batch_id}: {previous} -> {self.new_status}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Batch transitions are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Batch transitions cannot be deleted.')


class Planting(WorkspaceOwnedModel):
    """
    An abstract class for planting of seeds
    """
    planted = models.DateTimeField(default=timezone.now)
    seeds_used = models.ForeignKey(SeedPacket, on_delete=models.PROTECT)
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='%(class)s_sowings',
    )
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    location = None
    notes = models.TextField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.quantity} {self.seeds_used.seeds.plant_variety} planted {self.planted} in {self.location}'

    class Meta:
        abstract = True


class GardenRowDirectSowPlanting(Planting):
    """
    Planting via direct sow into a garden row
    """
    location = models.ForeignKey(GardenRow, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='row_sow_quantity_gte_1',
            ),
        ]


class GardenSquareDirectSowPlanting(Planting):
    """
    Planting via direct sow into a garden square
    """
    location = models.ForeignKey(GardenSquare, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='square_sow_quantity_gte_1',
            ),
        ]


class SeedTrayPlanting(Planting):
    """
    Planting into a seed tray
    """
    location = models.CharField(max_length=1024, null=True, blank=True)
    seed_tray = models.ForeignKey(SeedTray, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='seed_tray_quantity_gte_1',
            ),
        ]


class SeedTrayCellPlanting(models.Model):
    """
    Represents the number of seeds placed into a specific cell of a seed tray
    as part of a `SeedTrayPlanting` event. This lets us group an overall
    planting event while tracking per-cell quantities.
    """
    seed_tray_planting = models.ForeignKey(SeedTrayPlanting, on_delete=models.CASCADE, related_name='cell_plantings')
    cell = models.ForeignKey(SeedTrayCell, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seed_tray_planting', 'cell'], name='unique_cell_per_planting'),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='seed_tray_cell_quantity_gte_1',
            ),
        ]

    def __str__(self):
        return f'{self.quantity} in {self.cell} for planting {self.seed_tray_planting.pk}'


class SowingStockPosting(WorkspaceOwnedModel):
    """Immutable linkage from one sowing to each ledger audit row."""

    movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        related_name='sowing_posting',
    )
    row_planting = models.ForeignKey(
        GardenRowDirectSowPlanting,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_postings',
    )
    square_planting = models.ForeignKey(
        GardenSquareDirectSowPlanting,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_postings',
    )
    tray_planting = models.ForeignKey(
        SeedTrayPlanting,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_postings',
    )
    replacement_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='replacement',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(row_planting__isnull=False, square_planting__isnull=True, tray_planting__isnull=True),
                    models.Q(row_planting__isnull=True, square_planting__isnull=False, tray_planting__isnull=True),
                    models.Q(row_planting__isnull=True, square_planting__isnull=True, tray_planting__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='sowing_posting_exactly_one_planting',
            ),
        ]

    def clean(self):
        """Require the movement and selected planting in one workspace."""
        super().clean()
        errors = {}
        plantings = [
            self.row_planting,
            self.square_planting,
            self.tray_planting,
        ]
        selected = [planting for planting in plantings if planting is not None]
        if len(selected) != 1:
            errors['row_planting'] = 'Select exactly one sowing.'
        elif selected[0].workspace_id != self.workspace_id:
            errors['row_planting'] = 'The sowing belongs to a different workspace.'
        if self.movement_id and self.movement.workspace_id != self.workspace_id:
            errors['movement'] = 'The movement belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Sowing stock postings are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Sowing stock postings cannot be deleted.')


class SpecificPlant(WorkspaceOwnedModel):
    """
    A specific individual plant that has germinated from a seed tray cell.
    Created when germination is observed for a particular cell planting.
    """
    cell_planting = models.ForeignKey(SeedTrayCellPlanting, on_delete=models.PROTECT, related_name='specific_plants')
    germinated = models.DateTimeField(default=timezone.now)
    notes = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'Plant from {self.cell_planting} germinated {self.germinated}'


class SpecificPlantLocation(models.Model):
    """
    Tracks where a specific plant has been — a seed tray cell or garden square —
    and when it entered/left that location.
    """
    SEED_TRAY_CELL = 'seed_tray_cell'
    GARDEN_SQUARE = 'garden_square'
    LOCATION_TYPE_CHOICES = [
        (SEED_TRAY_CELL, 'Seed Tray Cell'),
        (GARDEN_SQUARE, 'Garden Square'),
    ]

    specific_plant = models.ForeignKey(SpecificPlant, on_delete=models.CASCADE, related_name='locations')
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES)
    seed_tray_cell = models.ForeignKey(SeedTrayCell, on_delete=models.PROTECT, null=True, blank=True)
    garden_square = models.ForeignKey(GardenSquare, on_delete=models.PROTECT, null=True, blank=True)
    started = models.DateTimeField(default=timezone.now)
    ended = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    def clean(self):
        super().clean()
        if self.location_type == self.SEED_TRAY_CELL:
            if self.seed_tray_cell is None:
                raise ValidationError({'seed_tray_cell': 'Required when location_type is seed_tray_cell.'})
            if self.garden_square is not None:
                raise ValidationError({'garden_square': 'Must be blank when location_type is seed_tray_cell.'})
        elif self.location_type == self.GARDEN_SQUARE:
            if self.garden_square is None:
                raise ValidationError({'garden_square': 'Required when location_type is garden_square.'})
            if self.seed_tray_cell is not None:
                raise ValidationError({'seed_tray_cell': 'Must be blank when location_type is garden_square.'})

        if self.ended is not None and self.ended < self.started:
            raise ValidationError({'ended': 'Must be on or after started.'})

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['specific_plant'],
                condition=models.Q(ended__isnull=True),
                name='unique_active_location_per_plant',
            ),
            models.CheckConstraint(
                condition=models.Q(ended__isnull=True) | models.Q(ended__gte=models.F('started')),
                name='location_ended_not_before_started',
            ),
        ]

    def __str__(self):
        loc = self.seed_tray_cell or self.garden_square
        return f'Plant {self.specific_plant_id} @ {loc} from {self.started}'


class PlantLifecycleEvent(WorkspaceOwnedModel):
    """One immutable fact about what happened to an individual plant.

    Lifecycle state is replayed from these events rather than stored, so no
    mutable status field can become a competing source of truth. Corrections
    append a reversal instead of editing or deleting the original fact.
    """

    class EventType(models.TextChoices):
        """Recorded lifecycle and disposition facts."""

        GERMINATED = 'germinated', 'Germinated'
        READY = 'ready', 'Ready for sale or use'
        TRANSPLANTED = 'transplanted', 'Transplanted or planted out'
        RETAINED = 'retained', 'Retained'
        FAILED = 'failed', 'Failed'
        CULLED = 'culled', 'Culled'
        DONATED = 'donated', 'Donated'
        HARVEST_FINISHED = 'harvest_finished', 'Harvest finished'
        CORRECTED = 'corrected', 'Corrected'

    plant = models.ForeignKey(
        SpecificPlant,
        on_delete=models.CASCADE,
        related_name='lifecycle_events',
    )
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        editable=False,
        related_name='plant_lifecycle_events',
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    occurred_at = models.DateTimeField()
    reason = models.TextField(blank=True, default='')
    reference = models.CharField(max_length=255, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    reversal_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversal',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_at', 'pk']
        indexes = [
            models.Index(fields=['plant', 'occurred_at'], name='plant_lifecycle_replay_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['plant'],
                condition=models.Q(event_type='germinated'),
                name='plant_lifecycle_single_germination',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(event_type='corrected', reversal_of__isnull=False),
                    ~models.Q(event_type='corrected') & models.Q(reversal_of__isnull=True),
                    _connector=models.Q.OR,
                ),
                name='plant_lifecycle_reversal_type',
            ),
            models.UniqueConstraint(
                fields=['plant', 'event_type', 'reference'],
                condition=~models.Q(reference=''),
                name='plant_lifecycle_reference_idempotent',
            ),
        ]

    def __str__(self):
        return f'Plant {self.plant_id}: {self.event_type} at {self.occurred_at}'

    def clean(self):
        """Keep the event, its plant, and its denormalised batch consistent."""
        super().clean()
        errors = {}
        if self.plant_id and self.plant.workspace_id != self.workspace_id:
            errors['plant'] = 'The plant belongs to a different workspace.'
        if self.batch_id:
            if self.batch.workspace_id != self.workspace_id:
                errors['batch'] = 'The batch belongs to a different workspace.'
            elif self.plant_id and self.batch_id != self.plant_batch_id():
                errors['batch'] = 'The batch does not match the batch that raised this plant.'
        errors.update(self._reversal_errors())
        if errors:
            raise ValidationError(errors)

    def _reversal_errors(self):
        """Reject a correction that does not name one of this plant's facts."""
        if self.reversal_of_id is None:
            return {}
        if self.reversal_of.plant_id != self.plant_id:
            return {'reversal_of': 'The corrected event belongs to a different plant.'}
        if self.reversal_of.event_type == self.EventType.CORRECTED:
            return {'reversal_of': 'A correction cannot itself be corrected.'}
        return {}

    def plant_batch_id(self):
        """Return the batch that raised this event's plant."""
        return self.plant.cell_planting.seed_tray_planting.batch_id

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Plant lifecycle events are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Plant lifecycle events cannot be deleted.')


class GardenSquareTransplant(WorkspaceOwnedModel):
    """
    Legacy aggregate transplant from a seed tray into a garden square.

    New transplant workflows use SpecificPlantLocation as their source of truth.
    """
    original_planting = models.ForeignKey(SeedTrayPlanting, on_delete=models.PROTECT)
    transplanted = models.DateTimeField(default=timezone.now)
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    location = models.ForeignKey(GardenSquare, on_delete=models.PROTECT)
    notes = models.TextField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='square_transplant_quantity_gte_1',
            ),
        ]

    def __str__(self):
        return f'{self.quantity} {self.original_planting.seeds_used.seeds.plant_variety} planted {self.original_planting.planted} transplanted {self.transplanted} in {self.location}'
