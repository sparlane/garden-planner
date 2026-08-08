"""An append-only subledger valuing each individual plant from its inputs.

Inputs are bought and applied in lots and batches, while nursery revenue is
earned one seedling at a time. These rows are the bridge: each one says that
some measured part of one source input — a lot of seed drawn by a sowing, a lot
of media put into cells, a remainder an operator threw away — belongs to one
tray cell, one plant, one batch's unresolved pool, or to production loss.

Three properties make the ledger trustworthy, and each is enforced rather than
documented:

- **Rows are immutable.** A wrong allocation is reversed and reposted, never
  edited, so what was reported last month stays readable next to its
  correction. This follows `inventory.models.StockMovement` and
  `plantings.models.PlantLifecycleEvent`, and reversal is expressed the same
  way they express it: a positive amount plus a `reversal_of` link rather than
  a negative number.
- **Cost is snapshotted.** `unit_cost` is copied from the lot when the layer is
  posted, for the reason `applications.models.InputApplicationLine` snapshots
  its rate: revaluing a lot afterwards must not silently rewrite what was
  already reported as inventory value or loss.
- **Unknown is not zero.** A lot with no recorded unit cost produces a layer
  with a null `unit_cost` and a null `amount`. Substituting zero would quietly
  understate every total built on it.

Provisional versus final is deliberately *not* a column here. These rows cannot
be edited, so flipping a stored flag at output finalization would mean reversing
and reposting every layer to change one field — doubling the ledger without
recording a single new fact. Finality is already a property of the batch:
`ProductionBatch.output_finalized_at` is an audited, timestamped transition, and
a layer is provisional exactly when its batch has not reached it.
"""

# pylint: disable=duplicate-code

import operator
from functools import reduce

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from applications.models import FACTOR_DECIMAL_PLACES, FACTOR_MAX_DIGITS, InputApplicationLine
from inventory.models import (
    COST_DECIMAL_PLACES,
    COST_MAX_DIGITS,
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    StockMovement,
)
from inventory.units import UnitCode
from plantings.models import ProductionBatch, SowingStockPosting, SpecificPlant
from seedtrays.models import SeedTrayCell, SeedTrayGeneration, SeedTrayGenerationResidual
from workspaces.models import WorkspaceOwnedModel


#: Columns that can hold the source input a layer draws from. Each name is also
#: the ``source_type`` value selecting it, which lets the identity constraint be
#: generated rather than written out once per source. It is kept in step with
#: `CostAllocation.SourceType` by a test.
SOURCE_FIELDS = ('application_line', 'sowing_posting', 'generation_residual')

#: Columns that can hold the thing a layer is allocated to. Same naming trick as
#: `SOURCE_FIELDS`, and kept in step with `CostAllocation.TargetType` by a test.
TARGET_FIELDS = ('seed_tray_cell', 'specific_plant')

#: Target types that name no individual thing. A batch pool is cost that has not
#: reached a cell or a plant yet; production loss is cost that never will; and
#: unattributed cost never could, because the batch produced something this
#: subledger does not individualise — a direct-sown row is a crop, not a set of
#: seedlings. Calling that last one a loss would report a Garden workspace's
#: entire harvest as waste, so it stays its own honest figure.
POOL_TARGET_TYPES = ('batch_pool', 'production_loss', 'unattributed')


class CostAllocationRun(WorkspaceOwnedModel):
    """One recalculation of one batch's allocations.

    A run is persisted only when it actually wrote layers. The reallocation is
    driven from ordinary events — posting an application, recording a
    germination, cleaning a tray — and most of those change nothing, so storing
    a row for every check would bury the runs that did something.
    """

    class Trigger(models.TextChoices):
        """What caused this recalculation."""

        APPLICATION_POSTED = 'application_posted', 'Input application posted'
        APPLICATION_REVERSED = 'application_reversed', 'Input application reversed'
        SOWING_POSTED = 'sowing_posted', 'Sowing posted'
        SOWING_CORRECTED = 'sowing_corrected', 'Sowing corrected'
        GERMINATION = 'germination', 'Germination observed'
        GENERATION_CLOSED = 'generation_closed', 'Tray generation cleaned'
        OUTPUT_FINALIZED = 'output_finalized', 'Batch output finalized'
        MANUAL_RECALCULATE = 'manual_recalculate', 'Recalculated by an operator'

    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='cost_allocation_runs',
    )
    trigger = models.CharField(max_length=24, choices=Trigger.choices)
    reason = models.TextField(blank=True, default='')
    posted_count = models.PositiveIntegerField(default=0)
    reversed_count = models.PositiveIntegerField(default=0)
    froze_output = models.BooleanField(default=False)
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
        indexes = [
            models.Index(fields=['batch', 'created'], name='cost_run_batch_idx'),
        ]

    def __str__(self):
        return f'Cost run {self.pk or "new"} for batch {self.batch_id} ({self.trigger})'

    def clean(self):
        """Keep the recalculated batch inside this run's workspace."""
        super().clean()
        if self.batch_id and self.batch.workspace_id != self.workspace_id:
            raise ValidationError({
                'batch': 'The batch belongs to a different workspace.',
            })

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Cost allocation runs are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Cost allocation runs cannot be deleted.')


class CostAllocation(WorkspaceOwnedModel):
    """One immutable layer of cost from one source input onto one thing.

    There is no generic foreign key on either side. Every supported source and
    every supported target has its own column, and generated check constraints
    require exactly one of each, which keeps both relationships enforceable by
    the database and protected against deletion — the same shape
    `applications.models.InputApplicationTarget` uses.
    """

    class SourceType(models.TextChoices):
        """Where the cost came from.

        Each value is also the name of the column that holds it, which is what
        lets the identity constraint be generated rather than written out.
        """

        APPLICATION_LINE = 'application_line', 'Input application line'
        SOWING_POSTING = 'sowing_posting', 'Sowing stock posting'
        GENERATION_RESIDUAL = 'generation_residual', 'Tray generation residual'

    class TargetType(models.TextChoices):
        """What the cost was allocated to.

        The first two are also column names, for the reason `SourceType`
        explains. The last two name no individual thing.
        """

        SEED_TRAY_CELL = 'seed_tray_cell', 'Tray cell'
        SPECIFIC_PLANT = 'specific_plant', 'Plant'
        BATCH_POOL = 'batch_pool', 'Unresolved batch pool'
        PRODUCTION_LOSS = 'production_loss', 'Production loss'
        UNATTRIBUTED = 'unattributed', 'Not attributable to a plant'

    class Basis(models.TextChoices):
        """How this layer's share of the source was arrived at."""

        SEEDS_SOWN = 'seeds_sown', 'Seeds or clusters sown'
        CELL_VOLUME = 'cell_volume', 'Cell volume'
        PER_PLANT = 'per_plant', 'Per plant'
        AREA = 'area', 'Target area'
        EQUAL_SHARE = 'equal_share', 'Equal share of a cell'
        DIRECT = 'direct', 'Whole source'

    run = models.ForeignKey(
        CostAllocationRun,
        on_delete=models.PROTECT,
        related_name='allocations',
    )
    batch = models.ForeignKey(
        ProductionBatch,
        on_delete=models.PROTECT,
        related_name='cost_allocations',
    )
    source_type = models.CharField(max_length=24, choices=SourceType.choices)
    application_line = models.ForeignKey(
        InputApplicationLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    sowing_posting = models.ForeignKey(
        SowingStockPosting,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    generation_residual = models.ForeignKey(
        SeedTrayGenerationResidual,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    # Nullable because a discarded remainder posts no movement at all: the
    # application already consumed it, and a second ledger row would report
    # twice what was used. `SeedTrayGenerationResidual` records that decision.
    movement = models.ForeignKey(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    target_type = models.CharField(max_length=24, choices=TargetType.choices)
    seed_tray_cell = models.ForeignKey(
        SeedTrayCell,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    seed_tray_generation = models.ForeignKey(
        SeedTrayGeneration,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
        help_text='The tray fill this cell was serving when the input went in.',
    )
    specific_plant = models.ForeignKey(
        SpecificPlant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    basis = models.CharField(max_length=16, choices=Basis.choices)
    basis_weight = models.DecimalField(
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
        help_text="This layer's share of the source, as a ratio.",
    )
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    base_unit = models.CharField(max_length=16, choices=UnitCode.choices)
    unit_cost = models.DecimalField(
        max_digits=COST_MAX_DIGITS,
        decimal_places=COST_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    currency_code = models.CharField(max_length=3)
    reversal_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversal',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        indexes = [
            models.Index(fields=['batch', 'target_type'], name='cost_allocation_batch_idx'),
            models.Index(fields=['specific_plant'], name='cost_allocation_plant_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=reduce(
                    operator.or_,
                    (
                        models.Q(
                            source_type=chosen,
                            **{
                                f'{field}__isnull': field != chosen
                                for field in SOURCE_FIELDS
                            },
                        )
                        for chosen in SOURCE_FIELDS
                    ),
                ),
                name='cost_allocation_source_identity',
            ),
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
                ) | models.Q(
                    target_type__in=POOL_TARGET_TYPES,
                    **{f'{field}__isnull': True for field in TARGET_FIELDS},
                ),
                name='cost_allocation_target_identity',
            ),
            models.CheckConstraint(
                condition=(models.Q(seed_tray_generation__isnull=True) | models.Q(target_type='seed_tray_cell')),
                name='cost_allocation_generation_needs_cell',
            ),
            models.CheckConstraint(
                condition=models.Q(basis_weight__gte=0),
                name='cost_allocation_nonnegative_weight',
            ),
            models.CheckConstraint(
                condition=models.Q(base_quantity__gte=0),
                name='cost_allocation_nonnegative_quantity',
            ),
            # An unknown lot cost produces an unknown amount. Pairing a known
            # unit cost with a null amount, or the reverse, would let a total
            # silently treat one of them as zero.
            models.CheckConstraint(
                condition=(models.Q(unit_cost__isnull=True, amount__isnull=True) | models.Q(unit_cost__isnull=False, amount__isnull=False)),
                name='cost_allocation_cost_and_amount_together',
            ),
        ]

    def __str__(self):
        return (
            f'{self.amount if self.amount is not None else "unknown"} '
            f'{self.currency_code} to {self.get_target_type_display().lower()}'
        )

    @property
    def source(self):
        """Return the one input this layer draws from."""
        return getattr(self, self.source_type, None)

    @property
    def source_id(self):
        """Return the primary key of the one input this layer draws from."""
        return getattr(self, f'{self.source_type}_id', None)

    @property
    def target(self):
        """Return the one thing this layer is allocated to, when there is one."""
        return getattr(self, self.target_type, None)

    @property
    def target_id(self):
        """Return the primary key of the target, or None for a pool."""
        return getattr(self, f'{self.target_type}_id', None)

    def clean(self):
        """Require one coherent source, one coherent target, and one workspace."""
        super().clean()
        errors = {}
        self._add_identity_errors(errors, SOURCE_FIELDS, 'source_type', required=True)
        self._add_identity_errors(errors, TARGET_FIELDS, 'target_type', required=False)
        self._add_workspace_errors(errors)
        self._add_generation_errors(errors)
        self._add_reversal_errors(errors)
        if errors:
            raise ValidationError(errors)

    def _add_identity_errors(self, errors, fields, type_field, required):
        """Require the populated column to be the one the type field declares."""
        declared = getattr(self, type_field)
        populated = [
            field for field in fields
            if getattr(self, f'{field}_id', None) is not None
        ]
        if declared in fields:
            if populated != [declared]:
                errors[type_field] = 'The declared type does not match the populated column.'
        elif required:
            errors[type_field] = f'Select a supported {type_field.replace("_", " ")}.'
        elif populated:
            errors[type_field] = 'A pool allocation names no individual target.'

    def _add_workspace_errors(self, errors):
        """Keep every referenced record inside this layer's workspace.

        A tray cell is reached through its tray, because cells are not workspace
        owned in their own right.
        """
        owners = {
            'run': self.run if self.run_id else None,
            'batch': self.batch if self.batch_id else None,
            'movement': self.movement if self.movement_id else None,
            'specific_plant': self.specific_plant if self.specific_plant_id else None,
            'seed_tray_generation': (
                self.seed_tray_generation if self.seed_tray_generation_id else None
            ),
            'seed_tray_cell': self.seed_tray_cell.tray if self.seed_tray_cell_id else None,
        }
        errors.update({
            field: f'The {field.replace("_", " ")} belongs to a different workspace.'
            for field, owner in owners.items()
            if owner is not None and owner.workspace_id != self.workspace_id
        })
        if self.run_id and self.batch_id and self.run.batch_id != self.batch_id:
            errors['run'] = 'The run recalculated a different batch.'

    def _add_generation_errors(self, errors):
        """Require the recorded fill to be one of the cell's own tray."""
        if self.seed_tray_generation_id is None:
            return
        if self.target_type != self.TargetType.SEED_TRAY_CELL:
            errors['seed_tray_generation'] = 'Only a cell allocation carries a fill.'
        elif self.seed_tray_cell_id and self.seed_tray_generation.tray_id != self.seed_tray_cell.tray_id:
            errors['seed_tray_generation'] = 'The fill belongs to a different tray.'

    def _add_reversal_errors(self, errors):
        """Reject a reversal that does not mirror the layer it cancels."""
        if self.reversal_of_id is None:
            return
        original = self.reversal_of
        if original.reversal_of_id is not None:
            errors['reversal_of'] = 'A reversal cannot itself be reversed.'
        elif original.batch_id != self.batch_id:
            errors['reversal_of'] = 'The reversed layer belongs to a different batch.'
        elif (original.amount, original.currency_code) != (self.amount, self.currency_code):
            errors['reversal_of'] = 'A reversal must carry the reversed amount.'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Cost allocations are immutable; reverse them instead.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Cost allocations cannot be deleted.')
