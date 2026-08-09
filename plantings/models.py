"""
Models for Plantings
"""
# pylint: disable=duplicate-code
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from inventory.models import (
    POSITIVE_DECIMAL,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    StockMovement,
)
from inventory.units import UnitCode
from locations.models import Location
from plants.models import PlantVariety
from seeds.models import SeedPacket
from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayGeneration
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

    The generation names the fill of the tray this sowing went into, so media
    applied to one cultivation cycle is never attributed to seedlings raised in
    the same cells during another. It is nullable because sowings recorded
    before generations existed have no truthful answer, and because a sowing
    can still be recorded without naming a tray at all.
    """
    location = models.CharField(max_length=1024, null=True, blank=True)
    seed_tray = models.ForeignKey(SeedTray, on_delete=models.PROTECT, null=True, blank=True)
    generation = models.ForeignKey(
        SeedTrayGeneration,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sowings',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='seed_tray_quantity_gte_1',
            ),
            models.CheckConstraint(
                condition=(models.Q(generation__isnull=True) | models.Q(seed_tray__isnull=False)),
                name='seed_tray_generation_requires_tray',
            ),
        ]

    def clean(self):
        """Keep a sowing's generation on the tray the sowing names."""
        super().clean()
        if self.generation_id and self.generation.tray_id != self.seed_tray_id:
            raise ValidationError({
                'generation': 'The generation belongs to a different tray.',
            })


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
    Tracks where a specific plant has been — a seed tray cell, a garden square,
    or a location in the catalog — and when it entered/left that location.

    A plant in a tray records the cell, not the bench the tray happens to stand
    on: the tray's own placement already says that, and duplicating it here
    would let the two disagree the moment a tray is moved. `location` is for a
    plant standing somewhere in its own right, such as a potted plant on a
    bench.
    """
    SEED_TRAY_CELL = 'seed_tray_cell'
    GARDEN_SQUARE = 'garden_square'
    LOCATION = 'location'
    LOCATION_TYPE_CHOICES = [
        (SEED_TRAY_CELL, 'Seed Tray Cell'),
        (GARDEN_SQUARE, 'Garden Square'),
        (LOCATION, 'Location'),
    ]

    #: For each location type, the field that must be set and those that must
    #: not be. One table drives both `clean()` and the API's field errors, so a
    #: fourth kind of place cannot be added to one and forgotten in the other.
    LOCATION_FIELDS = {
        SEED_TRAY_CELL: 'seed_tray_cell',
        GARDEN_SQUARE: 'garden_square',
        LOCATION: 'location',
    }

    specific_plant = models.ForeignKey(SpecificPlant, on_delete=models.CASCADE, related_name='locations')
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES)
    seed_tray_cell = models.ForeignKey(SeedTrayCell, on_delete=models.PROTECT, null=True, blank=True)
    garden_square = models.ForeignKey(GardenSquare, on_delete=models.PROTECT, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='standing_plants')
    started = models.DateTimeField(default=timezone.now)
    ended = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    override_reason = models.TextField(blank=True, default='')

    def clean(self):
        super().clean()
        required = self.LOCATION_FIELDS.get(self.location_type)
        if required is not None:
            if getattr(self, f'{required}_id') is None:
                raise ValidationError({required: f'Required when location_type is {self.location_type}.'})
            for field_name in self.LOCATION_FIELDS.values():
                if field_name == required:
                    continue
                if getattr(self, f'{field_name}_id') is not None:
                    raise ValidationError({field_name: f'Must be blank when location_type is {self.location_type}.'})

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
        loc = self.seed_tray_cell or self.garden_square or self.location
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


class BulkPlantOperation(WorkspaceOwnedModel):
    """One confirmed bulk action shared by independently audited plants."""

    class Action(models.TextChoices):
        """Actions currently backed by plant domain records."""

        GERMINATE = 'germinate', 'Germinate'
        MOVE = 'move', 'Move or transplant'
        READY = 'ready', 'Ready'
        RETAIN = 'retain', 'Retain'
        DONATE = 'donate', 'Donate'
        FAIL = 'fail', 'Fail'
        CULL = 'cull', 'Cull'
        FINISH_HARVEST = 'finish_harvest', 'Finish harvest'

    class Atomicity(models.TextChoices):
        """How conflicts in a confirmed selection are handled."""

        ALL_OR_NOTHING = 'all_or_nothing', 'All or nothing'
        ELIGIBLE_ONLY = 'eligible_only', 'Eligible only'

    idempotency_key = models.UUIDField()
    request_digest = models.CharField(max_length=64, editable=False)
    action = models.CharField(max_length=24, choices=Action.choices)
    atomicity = models.CharField(max_length=20, choices=Atomicity.choices)
    occurred_at = models.DateTimeField()
    reason = models.TextField(blank=True, default='')
    selection_source = models.JSONField(blank=True, default=dict)
    action_payload = models.JSONField(blank=True, default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'idempotency_key'],
                name='bulk_plant_operation_idempotent',
            ),
        ]

    def __str__(self):
        return f'{self.action} on {self.created}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Bulk plant operations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Bulk plant operations cannot be deleted.')


class BulkPlantOperationResult(WorkspaceOwnedModel):
    """The result one bulk operation produced for one concrete plant."""

    class Status(models.TextChoices):
        """Whether this member changed or was excluded by eligible-only mode."""

        APPLIED = 'applied', 'Applied'
        SKIPPED = 'skipped', 'Skipped'

    operation = models.ForeignKey(
        BulkPlantOperation,
        on_delete=models.PROTECT,
        related_name='results',
    )
    plant = models.ForeignKey(
        SpecificPlant,
        on_delete=models.PROTECT,
        related_name='bulk_operation_results',
    )
    status = models.CharField(max_length=12, choices=Status.choices)
    errors = models.JSONField(blank=True, default=list)
    lifecycle_event = models.ForeignKey(
        PlantLifecycleEvent,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='bulk_operation_results',
    )
    location = models.ForeignKey(
        SpecificPlantLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='bulk_operation_results',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['plant_id']
        constraints = [
            models.UniqueConstraint(
                fields=['operation', 'plant'],
                name='bulk_plant_operation_result_unique',
            ),
        ]

    def __str__(self):
        return f'{self.operation_id}: plant {self.plant_id} {self.status}'

    def clean(self):
        """Keep the operation, plant, and produced records in one workspace."""
        super().clean()
        errors = {}
        if self.operation_id and self.operation.workspace_id != self.workspace_id:
            errors['operation'] = 'The operation belongs to a different workspace.'
        if self.plant_id and self.plant.workspace_id != self.workspace_id:
            errors['plant'] = 'The plant belongs to a different workspace.'
        if self.lifecycle_event_id and self.lifecycle_event.plant_id != self.plant_id:
            errors['lifecycle_event'] = 'The event belongs to a different plant.'
        if self.location_id and self.location.specific_plant_id != self.plant_id:
            errors['location'] = 'The location belongs to a different plant.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Bulk plant operation results are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Bulk plant operation results cannot be deleted.')


#: The measurement units a crop yield may be recorded in. Seed units describe
#: what went in rather than what came out, and an area is not a yield, so
#: neither appears here.
HARVEST_UNIT_CODES = (
    UnitCode.EACH,
    UnitCode.GRAM,
    UnitCode.KILOGRAM,
    UnitCode.MILLILITRE,
    UnitCode.LITRE,
)

HARVEST_UNIT_CHOICES = [(code.value, code.label) for code in HARVEST_UNIT_CODES]


class Harvest(WorkspaceOwnedModel):
    """One measured crop yield taken from a production batch.

    A harvest is an observation rather than a document assembled from lines, so
    it is posted the moment it is recorded. A mistake is corrected by reversing
    the record, which keeps it visible while excluding it from every total.
    """

    class Status(models.TextChoices):
        """Whether this harvest still counts towards yield."""

        POSTED = 'posted', 'Posted'
        REVERSED = 'reversed', 'Reversed'

    class Grade(models.TextChoices):
        """The saleable class an operator assigned to this yield."""

        UNGRADED = 'ungraded', 'Ungraded'
        PREMIUM = 'premium', 'Premium'
        STANDARD = 'standard', 'Standard'
        SECONDS = 'seconds', 'Seconds'

    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='harvests',
    )
    harvested_at = models.DateTimeField()
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    unit_code = models.CharField(max_length=16, choices=HARVEST_UNIT_CHOICES)
    garden_square = models.ForeignKey(
        GardenSquare,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='harvests',
    )
    garden_row = models.ForeignKey(
        GardenRow,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='harvests',
    )
    quality_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    grade = models.CharField(
        max_length=16,
        choices=Grade.choices,
        default=Grade.UNGRADED,
    )
    notes = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.POSTED,
        editable=False,
    )
    posted_at = models.DateTimeField(default=timezone.now, editable=False)
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    reverse_reason = models.TextField(blank=True, default='', editable=False)
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
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-harvested_at', '-pk']
        indexes = [
            models.Index(fields=['batch', 'harvested_at'], name='harvest_batch_period_idx'),
            models.Index(fields=['workspace', 'harvested_at'], name='harvest_period_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='harvest_quantity_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(garden_square__isnull=True),
                    models.Q(garden_row__isnull=True),
                    _connector=models.Q.OR,
                ),
                name='harvest_single_location',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    unit_code__in=[code.value for code in HARVEST_UNIT_CODES],
                ),
                name='harvest_allowed_unit',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(status='posted', reversed_at__isnull=True),
                    models.Q(status='reversed', reversed_at__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='harvest_reversal_stamp',
            ),
        ]

    def __str__(self):
        return f'{self.quantity} {self.unit_code} from {self.batch} on {self.harvested_at}'

    def clean(self):
        """Keep the batch, the location, and the unit inside this workspace."""
        super().clean()
        errors = {}
        if self.batch_id and self.batch.workspace_id != self.workspace_id:
            errors['batch'] = 'The batch belongs to a different workspace.'
        if self.garden_square_id and self.garden_row_id:
            errors['garden_row'] = 'Record a garden square or a garden row, not both.'
        for field in ('garden_square', 'garden_row'):
            location = getattr(self, field, None)
            if location is not None and location.workspace_id != self.workspace_id:
                errors[field] = 'The location belongs to a different workspace.'
        if self.unit_code not in {code.value for code in HARVEST_UNIT_CODES}:
            errors['unit_code'] = 'Record a harvest in each, g, kg, ml, or l.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Harvests are immutable; reverse them instead.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Harvests cannot be deleted; reverse them instead.')


class HarvestPlant(models.Model):
    """One individual plant a harvest is attributed to.

    The allocation records attribution and nothing more. A measured kilogram is
    never split across the plants it came from, because that division was not
    observed and inventing it would misreport every per-plant total.
    """

    harvest = models.ForeignKey(
        Harvest,
        on_delete=models.PROTECT,
        related_name='plant_allocations',
    )
    plant = models.ForeignKey(
        SpecificPlant,
        on_delete=models.PROTECT,
        related_name='harvest_allocations',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['harvest', 'plant']
        constraints = [
            models.UniqueConstraint(
                fields=['harvest', 'plant'],
                name='harvest_plant_unique',
            ),
        ]

    def __str__(self):
        return f'Harvest {self.harvest_id} from plant {self.plant_id}'

    def clean(self):
        """Require a plant this workspace raised in this harvest's batch."""
        super().clean()
        if not self.harvest_id or not self.plant_id:
            return
        errors = {}
        if self.plant.workspace_id != self.harvest.workspace_id:
            errors['plant'] = 'The plant belongs to a different workspace.'
        elif self.plant_batch_id() != self.harvest.batch_id:
            errors['plant'] = "The plant was not raised by this harvest's batch."
        if errors:
            raise ValidationError(errors)

    def plant_batch_id(self):
        """Return the batch that raised this allocation's plant."""
        return self.plant.cell_planting.seed_tray_planting.batch_id

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Harvest allocations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Harvest allocations cannot be deleted.')


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
