"""
Models for Plantings
"""
# pylint: disable=duplicate-code,too-many-lines
from decimal import Decimal

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
    cell_planting = models.ForeignKey(
        SeedTrayCellPlanting,
        on_delete=models.PROTECT,
        related_name='specific_plants',
        null=True,
        blank=True,
    )
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='specific_plants',
        null=True,
        blank=True,
        editable=False,
    )
    promoted_from_cohort = models.ForeignKey(
        'PlantCohort',
        on_delete=models.PROTECT,
        related_name='promoted_plants',
        null=True,
        blank=True,
        editable=False,
    )
    germinated = models.DateTimeField(default=timezone.now)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(cell_planting__isnull=False, promoted_from_cohort__isnull=True),
                    models.Q(cell_planting__isnull=True, promoted_from_cohort__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='specific_plant_exactly_one_origin',
            ),
        ]

    def clean(self):
        """Keep the durable batch and whichever origin raised this plant aligned."""
        super().clean()
        origins = [self.cell_planting_id is not None, self.promoted_from_cohort_id is not None]
        if sum(origins) != 1:
            raise ValidationError('A plant must have exactly one tray-cell or cohort origin.')
        origin_batch_id = (
            self.cell_planting.seed_tray_planting.batch_id
            if self.cell_planting_id else self.promoted_from_cohort.batch_id
        )
        if self.batch_id is None:
            setattr(self, 'batch_id', origin_batch_id)
        elif self.batch_id != origin_batch_id:
            raise ValidationError({'batch': 'The batch does not match the plant origin.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        origin = self.cell_planting or f'cohort {self.promoted_from_cohort_id}'
        return f'Plant from {origin} germinated {self.germinated}'


class GrowthStage(WorkspaceOwnedModel):
    """One workspace-configurable operational nursery stage."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    display_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    target_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['display_order', 'name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='growth_stage_workspace_code_unique',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.code = self.code.strip().lower()
        if not self.code:
            raise ValidationError({'code': 'A stable code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryPlanningAssumption(WorkspaceOwnedModel):
    """Effective-dated yield and density assumptions for one variety."""

    variety = models.ForeignKey(
        PlantVariety, on_delete=models.PROTECT, related_name='nursery_planning_assumptions',
    )
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    germination_rate = models.DecimalField(
        max_digits=7, decimal_places=6,
        validators=[MinValueValidator(POSITIVE_DECIMAL), MaxValueValidator(1)],
    )
    seeds_per_cluster = models.PositiveIntegerField(default=1)
    tray_density = models.PositiveIntegerField(
        help_text='Seed clusters planned per tray.',
    )
    notes = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['variety__name', '-effective_from', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'variety', 'effective_from'],
                name='nursery_assumption_variety_effective_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(germination_rate__gt=0, germination_rate__lte=1),
                name='nursery_assumption_germination_rate_valid',
            ),
        ]

    def __str__(self):
        return f'{self.variety} from {self.effective_from}'

    def clean(self):
        """Keep the effective range and variety inside the workspace."""
        super().clean()
        errors = {}
        if self.variety_id and self.variety.workspace_id != self.workspace_id:
            errors['variety'] = 'The variety belongs to another workspace.'
        if self.effective_until and self.effective_until < self.effective_from:
            errors['effective_until'] = 'The end date cannot precede the start date.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryPlanningStageAssumption(models.Model):
    """Loss, timing, and space assumptions for one production stage."""

    assumption = models.ForeignKey(
        NurseryPlanningAssumption, on_delete=models.PROTECT, related_name='stages',
    )
    stage = models.ForeignKey(
        GrowthStage, on_delete=models.PROTECT, related_name='planning_assumptions',
    )
    sequence = models.PositiveIntegerField()
    lead_days = models.PositiveIntegerField()
    loss_rate = models.DecimalField(
        max_digits=7, decimal_places=6, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('0.999999'))],
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True,
        related_name='planning_stage_assumptions',
    )
    capacity_basis = models.CharField(
        max_length=16, choices=Location.CapacityBasis.choices,
        default=Location.CapacityBasis.PLANTS,
    )
    capacity_per_plant = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal('1'),
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )

    class Meta:
        ordering = ['sequence', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['assumption', 'sequence'], name='nursery_assumption_stage_sequence_unique',
            ),
            models.UniqueConstraint(
                fields=['assumption', 'stage'], name='nursery_assumption_stage_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(loss_rate__gte=0, loss_rate__lt=1),
                name='nursery_stage_loss_rate_valid',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.assumption_id and self.stage_id:
            if self.stage.workspace_id != self.assumption.workspace_id:
                errors['stage'] = 'The stage belongs to another workspace.'
        if self.assumption_id and self.location_id:
            if self.location.workspace_id != self.assumption.workspace_id:
                errors['location'] = 'The location belongs to another workspace.'
        if self.capacity_basis == Location.CapacityBasis.NONE:
            errors['capacity_basis'] = 'Choose a measurable capacity basis.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryPlanningInputAssumption(models.Model):
    """Expected physical input required for each plant entering production."""

    assumption = models.ForeignKey(
        NurseryPlanningAssumption, on_delete=models.PROTECT, related_name='inputs',
    )
    item = models.ForeignKey(
        'inventory.InventoryItem', on_delete=models.PROTECT,
        related_name='nursery_planning_assumptions',
    )
    quantity_per_plant = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )

    class Meta:
        ordering = ['item__name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['assumption', 'item'], name='nursery_assumption_input_unique',
            ),
        ]

    def clean(self):
        super().clean()
        if self.assumption_id and self.item_id:
            if self.item.workspace_id != self.assumption.workspace_id:
                raise ValidationError({'item': 'The item belongs to another workspace.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryProductionPlan(WorkspaceOwnedModel):
    """One immutable-on-approval version of a nursery production plan."""

    class Status(models.TextChoices):
        """Whether this version can still change."""

        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'

    class Direction(models.TextChoices):
        """Which date anchors milestone scheduling."""

        BACKWARD = 'backward', 'Backward from ready window'
        FORWARD = 'forward', 'Forward from sowing date'

    code = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False,
    )
    direction = models.CharField(
        max_length=16, choices=Direction.choices, default=Direction.BACKWARD,
    )
    sowing_date = models.DateField(null=True, blank=True)
    supersedes = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='revisions', editable=False,
    )
    notes = models.TextField(blank=True, default='')
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code', '-version', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code', 'version'],
                name='nursery_plan_workspace_code_version_unique',
            ),
        ]

    def __str__(self):
        return f'{self.code} v{self.version}'

    def clean(self):
        super().clean()
        errors = {}
        if not self.code.strip():
            errors['code'] = 'A plan code is required.'
        if self.direction == self.Direction.FORWARD and not self.sowing_date:
            errors['sowing_date'] = 'A forward plan requires a sowing date.'
        if self.supersedes_id:
            if self.supersedes.workspace_id != self.workspace_id:
                errors['supersedes'] = 'The previous version belongs to another workspace.'
            elif self.supersedes.code != self.code:
                errors['supersedes'] = 'A revision must retain the plan code.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status=self.Status.APPROVED).exists():
            raise ValidationError('Approved plans are immutable; create a new version.')
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryPlanDemand(models.Model):
    """One committed, forecast, or manual demand input kept distinct."""

    class Source(models.TextChoices):
        """Commercial certainty of one demand input."""

        CONFIRMED_ORDER = 'confirmed_order', 'Confirmed order'
        FORECAST = 'forecast', 'Forecast'
        MANUAL = 'manual', 'Manual'

    class Priority(models.IntegerChoices):
        """Relative production urgency."""

        LOW = 10, 'Low'
        NORMAL = 20, 'Normal'
        HIGH = 30, 'High'
        URGENT = 40, 'Urgent'

    plan = models.ForeignKey(
        NurseryProductionPlan, on_delete=models.PROTECT, related_name='demand_lines',
    )
    variety = models.ForeignKey(
        PlantVariety, on_delete=models.PROTECT, related_name='nursery_plan_demand',
    )
    product_reference = models.CharField(max_length=255, blank=True, default='')
    target_quantity = models.PositiveIntegerField()
    ready_from = models.DateField()
    ready_until = models.DateField()
    source = models.CharField(max_length=24, choices=Source.choices)
    priority = models.IntegerField(choices=Priority.choices, default=Priority.NORMAL)
    customer_reference = models.CharField(max_length=255, blank=True, default='')
    order_reference = models.CharField(max_length=255, blank=True, default='')
    source_line_reference = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-priority', 'ready_from', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'source', 'source_line_reference'],
                condition=~models.Q(source_line_reference=''),
                name='nursery_plan_demand_source_line_unique',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.plan_id and self.variety_id:
            if self.variety.workspace_id != self.plan.workspace_id:
                errors['variety'] = 'The variety belongs to another workspace.'
        if self.ready_until < self.ready_from:
            errors['ready_until'] = 'The ready window cannot end before it starts.'
        if self.source == self.Source.CONFIRMED_ORDER and not self.order_reference:
            errors['order_reference'] = 'Confirmed demand requires an order reference.'
        if self.plan_id and self.plan.status == NurseryProductionPlan.Status.APPROVED:
            errors['plan'] = 'Approved plan demand is immutable.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class NurseryPlanRequirement(models.Model):
    """Calculated and approved snapshot for one demand line."""

    demand = models.OneToOneField(
        NurseryPlanDemand, on_delete=models.CASCADE, related_name='requirement',
    )
    assumption = models.ForeignKey(
        NurseryPlanningAssumption, on_delete=models.PROTECT,
        related_name='plan_requirements',
    )
    required_seeds = models.PositiveIntegerField()
    required_clusters = models.PositiveIntegerField()
    required_trays = models.PositiveIntegerField()
    expected_finished = models.PositiveIntegerField()
    sowing_date = models.DateField()
    expected_ready_from = models.DateField()
    expected_ready_until = models.DateField()
    assumption_snapshot = models.JSONField(default=dict)
    batch = models.OneToOneField(
        ProductionBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name='planning_requirement',
    )

    class Meta:
        ordering = ['sowing_date', 'pk']


class NurseryPlanMilestone(models.Model):
    """Calculated stage quantity, date, and capacity usage."""

    requirement = models.ForeignKey(
        NurseryPlanRequirement, on_delete=models.CASCADE, related_name='milestones',
    )
    stage = models.ForeignKey(
        GrowthStage, on_delete=models.PROTECT, related_name='plan_milestones',
    )
    sequence = models.PositiveIntegerField()
    planned_date = models.DateField()
    input_quantity = models.PositiveIntegerField()
    expected_output = models.PositiveIntegerField()
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True,
        related_name='plan_milestones',
    )
    capacity_basis = models.CharField(max_length=16, choices=Location.CapacityBasis.choices)
    capacity_required = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        ordering = ['planned_date', 'sequence', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['requirement', 'sequence'], name='nursery_plan_milestone_sequence_unique',
            ),
        ]


class NurseryPlanInputRequirement(models.Model):
    """Calculated item requirement retained with the plan version."""

    requirement = models.ForeignKey(
        NurseryPlanRequirement, on_delete=models.CASCADE, related_name='inputs',
    )
    item = models.ForeignKey(
        'inventory.InventoryItem', on_delete=models.PROTECT,
        related_name='nursery_plan_requirements',
    )
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
    )

    class Meta:
        ordering = ['item__name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['requirement', 'item'], name='nursery_plan_input_requirement_unique',
            ),
        ]


class NurseryPlanIssue(models.Model):
    """One stock or capacity conflict found during the last calculation."""

    class Kind(models.TextChoices):
        """Resources that may prevent the plan from being fulfilled."""

        SEED = 'seed', 'Seed shortage'
        INPUT = 'input', 'Input shortage'
        TRAY = 'tray', 'Tray shortage'
        CAPACITY = 'capacity', 'Location capacity conflict'
        ASSUMPTION = 'assumption', 'Missing assumption'

    plan = models.ForeignKey(
        NurseryProductionPlan, on_delete=models.CASCADE, related_name='issues',
    )
    demand = models.ForeignKey(
        NurseryPlanDemand, on_delete=models.CASCADE, null=True, blank=True,
        related_name='issues',
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    message = models.TextField()
    required_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True, blank=True,
    )
    available_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True, blank=True,
    )

    class Meta:
        ordering = ['kind', 'pk']


class PlantGrade(WorkspaceOwnedModel):
    """One workspace-configurable commercial plant grade."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    display_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='plant_grade_workspace_code_unique',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.code = self.code.strip().lower()
        if not self.code:
            raise ValidationError({'code': 'A stable code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PlantCohort(WorkspaceOwnedModel):
    """A homogeneous quantity of nursery plants managed under one identity."""

    class LifecycleState(models.TextChoices):
        """Commercial state shared by every plant represented by the cohort."""

        GROWING = 'growing', 'Growing'
        AVAILABLE = 'available', 'Available'
        RETAINED = 'retained', 'Retained'
        DEPLETED = 'depleted', 'Depleted'

    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='cohorts',
    )
    source_sowing = models.ForeignKey(
        SeedTrayPlanting,
        on_delete=models.PROTECT,
        related_name='cohorts',
        null=True,
        blank=True,
    )
    quantity = models.PositiveIntegerField(default=0, editable=False)
    lifecycle_state = models.CharField(
        max_length=16,
        choices=LifecycleState.choices,
        default=LifecycleState.GROWING,
        editable=False,
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='plant_cohorts',
        null=True,
        blank=True,
    )
    observed_at = models.DateTimeField(default=timezone.now, editable=False)
    revision = models.PositiveBigIntegerField(default=1, editable=False)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name='plant_cohort_quantity_nonnegative',
            ),
        ]

    def __str__(self):
        return f'Cohort {self.pk}: {self.quantity} {self.batch.variety}'

    def clean(self):
        """Keep the batch, source sowing, and current location in one workspace."""
        super().clean()
        errors = {}
        for field in ('batch', 'source_sowing', 'location'):
            value = getattr(self, field, None)
            if value is not None and value.workspace_id != self.workspace_id:
                errors[field] = f'The {field.replace("_", " ")} belongs to another workspace.'
        if self.source_sowing_id and self.source_sowing.batch_id != self.batch_id:
            errors['source_sowing'] = 'The sowing belongs to a different batch.'
        if self.quantity == 0 and self.lifecycle_state != self.LifecycleState.DEPLETED:
            errors['lifecycle_state'] = 'An empty cohort must be depleted.'
        if self.quantity > 0 and self.lifecycle_state == self.LifecycleState.DEPLETED:
            errors['lifecycle_state'] = 'A positive cohort cannot be depleted.'
        if errors:
            raise ValidationError(errors)


class CohortOperation(WorkspaceOwnedModel):
    """One immutable command that changed one or more cohorts."""

    class Action(models.TextChoices):
        """Supported cohort facts and structural operations."""

        OBSERVE = 'observe', 'Observe'
        ADJUST = 'adjust', 'Count adjustment'
        SPLIT = 'split', 'Split'
        MERGE = 'merge', 'Merge'
        MOVE = 'move', 'Move'
        READY = 'ready', 'Ready'
        RETAIN = 'retain', 'Retain'
        LOSS = 'loss', 'Loss'
        PROMOTE = 'promote', 'Promote'

    idempotency_key = models.UUIDField()
    action = models.CharField(max_length=16, choices=Action.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    reason = models.TextField(blank=True, default='')
    payload = models.JSONField(blank=True, default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'idempotency_key'],
                name='cohort_operation_workspace_idempotent',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Cohort operations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Cohort operations cannot be deleted.')


class CohortEvent(WorkspaceOwnedModel):
    """The immutable before-and-after entry for one cohort in an operation."""

    operation = models.ForeignKey(CohortOperation, on_delete=models.PROTECT, related_name='events')
    cohort = models.ForeignKey(PlantCohort, on_delete=models.PROTECT, related_name='events')
    quantity_before = models.PositiveIntegerField()
    quantity_delta = models.IntegerField()
    quantity_after = models.PositiveIntegerField()
    state_before = models.CharField(max_length=16, choices=PlantCohort.LifecycleState.choices)
    state_after = models.CharField(max_length=16, choices=PlantCohort.LifecycleState.choices)
    location_before = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    location_after = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    source_cohorts = models.ManyToManyField(PlantCohort, blank=True, related_name='lineage_events')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity_after__gte=0), name='cohort_event_after_nonnegative'),
            models.UniqueConstraint(fields=['operation', 'cohort'], name='cohort_event_operation_cohort_unique'),
        ]

    def clean(self):
        """Require arithmetic and ownership to match the containing operation."""
        super().clean()
        errors = {}
        if self.quantity_before + self.quantity_delta != self.quantity_after:
            errors['quantity_after'] = 'The event quantity does not reconcile.'
        if self.operation_id and self.operation.workspace_id != self.workspace_id:
            errors['operation'] = 'The operation belongs to another workspace.'
        if self.cohort_id and self.cohort.workspace_id != self.workspace_id:
            errors['cohort'] = 'The cohort belongs to another workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Cohort events are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Cohort events cannot be deleted.')


class NurseryObservation(WorkspaceOwnedModel):
    """An immutable dated nursery fact, optionally replacing one mistake."""

    stage = models.ForeignKey(
        GrowthStage, on_delete=models.PROTECT, null=True, blank=True,
        related_name='observations',
    )
    grade = models.ForeignKey(
        PlantGrade, on_delete=models.PROTECT, null=True, blank=True,
        related_name='observations',
    )
    container_item = models.ForeignKey(
        'inventory.InventoryItem', on_delete=models.PROTECT, null=True, blank=True,
        related_name='nursery_observations',
    )
    container_count = models.PositiveIntegerField(null=True, blank=True)
    container_name = models.CharField(max_length=255, blank=True, default='')
    container_size_label = models.CharField(max_length=64, blank=True, default='')
    container_volume_ml = models.PositiveIntegerField(null=True, blank=True)
    container_footprint_m2 = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
    )
    height_cm = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    spread_cm = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    root_condition = models.CharField(max_length=255, blank=True, default='')
    expected_ready = models.DateField(null=True, blank=True)
    photo_url = models.URLField(blank=True, default='')
    occurred_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, default='')
    input_application = models.ForeignKey(
        'applications.InputApplication', on_delete=models.PROTECT,
        null=True, blank=True, related_name='nursery_observations',
    )
    corrects = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='correction',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_at', 'pk']

    def clean(self):
        """Validate snapshots, ownership, correction direction, and content."""
        super().clean()
        errors = {}
        for field in ('stage', 'grade', 'container_item', 'input_application'):
            value = getattr(self, field, None)
            if value is not None and value.workspace_id != self.workspace_id:
                errors[field] = f'The {field.replace("_", " ")} belongs to another workspace.'
        if self.container_item_id:
            if self.container_item.category != self.container_item.Category.POT_CONTAINER:
                errors['container_item'] = 'Choose a pot or container inventory item.'
            if not self.container_count:
                errors['container_count'] = 'Record how many containers are assigned.'
        elif self.container_count is not None:
            errors['container_item'] = 'Choose the assigned container item.'
        facts = (
            self.stage_id, self.grade_id, self.container_item_id,
            self.height_cm, self.spread_cm, self.root_condition,
            self.expected_ready, self.photo_url, self.notes,
        )
        if not any(value not in (None, '') for value in facts):
            errors['notes'] = 'Record at least one nursery observation.'
        if self.corrects_id:
            if self.corrects.workspace_id != self.workspace_id:
                errors['corrects'] = 'The corrected observation belongs to another workspace.'
            elif self.corrects_id == self.pk:
                errors['corrects'] = 'An observation cannot correct itself.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Nursery observations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Nursery observations cannot be deleted.')


class NurseryObservationTarget(models.Model):
    """One plant or cohort sharing the facts on an observation."""

    observation = models.ForeignKey(
        NurseryObservation, on_delete=models.PROTECT, related_name='targets',
    )
    plant = models.ForeignKey(
        SpecificPlant, on_delete=models.PROTECT, null=True, blank=True,
        related_name='nursery_observation_targets',
    )
    cohort = models.ForeignKey(
        PlantCohort, on_delete=models.PROTECT, null=True, blank=True,
        related_name='nursery_observation_targets',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    models.Q(plant__isnull=False, cohort__isnull=True),
                    models.Q(plant__isnull=True, cohort__isnull=False),
                    _connector=models.Q.OR,
                ),
                name='nursery_observation_target_one_identity',
            ),
            models.UniqueConstraint(
                fields=['observation', 'plant'],
                condition=models.Q(plant__isnull=False),
                name='nursery_observation_target_unique_plant',
            ),
            models.UniqueConstraint(
                fields=['observation', 'cohort'],
                condition=models.Q(cohort__isnull=False),
                name='nursery_observation_target_unique_cohort',
            ),
        ]

    @property
    def target(self):
        """Return the concrete plant or cohort selected by this row."""
        return self.plant or self.cohort

    def clean(self):
        super().clean()
        populated = [self.plant_id is not None, self.cohort_id is not None]
        if sum(populated) != 1:
            raise ValidationError('Choose exactly one plant or cohort.')
        if self.observation_id and self.target.workspace_id != self.observation.workspace_id:
            raise ValidationError('The target belongs to another workspace.')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Nursery observation targets are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Nursery observation targets cannot be deleted.')


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
        LOST = 'lost', 'Lost during stocktake'
        CULLED = 'culled', 'Culled'
        DONATED = 'donated', 'Donated'
        HARVEST_FINISHED = 'harvest_finished', 'Harvest finished'
        SOLD = 'sold', 'Sold'
        RETURNED_AVAILABLE = 'returned_available', 'Returned available'
        RETURNED_QUARANTINED = 'returned_quarantined', 'Returned quarantined'
        RETURNED_DISCARDED = 'returned_discarded', 'Returned discarded'
        RELEASED_AVAILABLE = 'released_available', 'Released from quarantine'
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
    event_type = models.CharField(max_length=24, choices=EventType.choices)
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
            models.Index(fields=['workspace', 'occurred_at'], name='plant_event_report_idx'),
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
        return self.plant.batch_id or self.plant.cell_planting.seed_tray_planting.batch_id

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
        STAGE = 'stage', 'Update growth stage'
        GRADE = 'grade', 'Update grade'
        REPOT = 'repot', 'Pot on or repot'
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
    nursery_observation = models.ForeignKey(
        NurseryObservation,
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
        if self.nursery_observation_id and not self.nursery_observation.targets.filter(plant=self.plant).exists():
            errors['nursery_observation'] = 'The observation does not include this plant.'
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
