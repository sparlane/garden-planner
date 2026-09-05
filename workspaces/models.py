"""Workspace ownership and deployment profile models."""

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


#: Ledger quantity precision, restated here rather than imported. Inventory
#: depends on this module for workspace ownership, so importing its constants
#: back would close a cycle. `inventory.models` is the definition of record and
#: a test keeps the two in step.
QUANTITY_MAX_DIGITS = 24
QUANTITY_DECIMAL_PLACES = 9


def validate_iana_timezone(value):
    """Require a timezone name understood by the system IANA database."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError('Enter a valid IANA timezone name.') from exc


class Workspace(models.Model):
    """The ownership and configuration root for one deployment."""

    class Mode(models.TextChoices):
        """Supported product profiles."""

        GARDEN = 'garden', 'Garden'
        NURSERY = 'nursery', 'Nursery'

    class SetupState(models.TextChoices):
        """How far the guided garden setup has got.

        This records the gardener's answer, not the state of their data. A
        workspace with no garden yet is only offered setup while this is
        ``pending``, so somebody who declined once is not asked again, and
        somebody who finished can still reopen it to add another area.
        """

        PENDING = 'pending', 'Not started'
        SKIPPED = 'skipped', 'Skipped'
        COMPLETE = 'complete', 'Complete'

    class MeasurementSystem(models.TextChoices):
        """Supported display measurement systems."""

        METRIC = 'metric', 'Metric'
        IMPERIAL = 'imperial', 'Imperial'

    class GardenExperience(models.TextChoices):
        """How much stock, costing, and traceability detail Garden screens show.

        Meaningful only while ``mode`` is Garden: a Nursery workspace behaves
        as Advanced regardless of this value, because those workflows need
        the underlying records. Use ``is_advanced`` rather than comparing
        this field directly, so that rule lives in one place.
        """

        BASIC = 'basic', 'Basic'
        ADVANCED = 'advanced', 'Advanced'

    name = models.CharField(max_length=255)
    legal_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=(
            'The registered legal name of the entity making supplies, if it '
            'differs from the name this workspace is known by. A taxable '
            'supply document is issued under it.'
        ),
    )
    trading_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=(
            'The name the business trades under, where that differs from its '
            'legal name. Shown on a document alongside the legal name rather '
            'than instead of it.'
        ),
    )
    business_address = models.TextField(
        blank=True,
        default='',
        help_text=(
            'The seller address printed on taxable supply and correction '
            'documents.'
        ),
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.GARDEN,
    )
    garden_experience = models.CharField(
        max_length=16,
        choices=GardenExperience.choices,
        default=GardenExperience.BASIC,
        help_text=(
            'Whether Garden-profile screens show simplified defaults or full '
            'stock, costing, and traceability detail. Ignored in Nursery '
            'profile, which always behaves as Advanced.'
        ),
    )
    currency_code = models.CharField(
        max_length=3,
        default='USD',
        validators=[
            RegexValidator(
                regex=r'^[A-Z]{3}$',
                message='Enter a three-letter uppercase ISO 4217 currency code.',
            ),
        ],
    )
    default_tax_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('0'),
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('100')),
        ],
        help_text='Default tax percentage from 0 through 100.',
    )
    sales_prices_include_tax = models.BooleanField(
        default=False,
        help_text='Whether prices entered on new sales orders include tax.',
    )
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        validators=[validate_iana_timezone],
    )
    measurement_system = models.CharField(
        max_length=16,
        choices=MeasurementSystem.choices,
        default=MeasurementSystem.METRIC,
    )
    override_tolerance_percent = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('5'),
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('100')),
        ],
        help_text=(
            'How far a confirmed input quantity may differ from the calculated '
            'suggestion, as a percentage, before a reason is required.'
        ),
    )
    override_tolerance_floor = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text=(
            'Smallest difference in an item base unit that can require a '
            'reason, so a rounding-sized drift never does. Zero disables it.'
        ),
    )
    assumption_tolerance_percent = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('10'),
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('100')),
        ],
        help_text=(
            'How far an observed planning figure may differ from the assumption '
            'that predicted it, as a percentage of the assumption, before the '
            'variance report flags it for revision.'
        ),
    )
    assumption_minimum_samples = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1)],
        help_text=(
            'Smallest number of batches behind an observed figure that can '
            'raise a flag, so three trays never look like evidence.'
        ),
    )
    stocktake_two_person_required = models.BooleanField(
        default=False,
        help_text=(
            'Require a stocktake reviewer to be different from every counter.'
        ),
    )
    garden_setup_state = models.CharField(
        max_length=16,
        choices=SetupState.choices,
        default=SetupState.PENDING,
        help_text=(
            'Whether the guided garden setup has been finished or declined. '
            'An established workspace is never offered it, because it already '
            'has a garden.'
        ),
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(override_tolerance_percent__gte=0, override_tolerance_percent__lte=100),
                name='workspace_override_percent_range',
            ),
            models.CheckConstraint(
                condition=models.Q(override_tolerance_floor__gte=0),
                name='workspace_override_floor_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    assumption_tolerance_percent__gte=0,
                    assumption_tolerance_percent__lte=100,
                ),
                name='workspace_assumption_percent_range',
            ),
            models.CheckConstraint(
                condition=models.Q(assumption_minimum_samples__gte=1),
                name='workspace_assumption_samples_positive',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_advanced(self):
        """Whether stock, costing, and traceability detail should be shown.

        True for every Nursery workspace, and for a Garden workspace whose
        gardener has opted into Advanced. Callers should use this instead of
        comparing ``mode`` and ``garden_experience`` separately.
        """
        return self.mode == self.Mode.NURSERY or self.garden_experience == self.GardenExperience.ADVANCED


def get_default_workspace_id():
    """Return the configured workspace ID for direct ORM-created records.

    Only the primary key is touched, never the whole row. Migrations across
    every app carry this as a column default and evaluate it while the schema
    is mid-flight, so selecting all of Workspace would fail as soon as one
    migration adds a column that the database has not reached yet.
    """
    workspace_id = settings.CURRENT_WORKSPACE_ID
    if not Workspace.objects.filter(pk=workspace_id).exists():
        raise ImproperlyConfigured(
            f'CURRENT_WORKSPACE_ID={workspace_id} does not identify a workspace.'
        )
    return workspace_id


def get_current_workspace():
    """Return the single workspace configured for this deployment."""
    workspace_id = settings.CURRENT_WORKSPACE_ID
    try:
        return Workspace.objects.get(pk=workspace_id)
    except Workspace.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f'CURRENT_WORKSPACE_ID={workspace_id} does not identify a workspace.'
        ) from exc


class WorkspaceOwnedModel(models.Model):
    """Abstract base for independently addressable workspace data."""

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.PROTECT,
        default=get_default_workspace_id,
        editable=False,
        related_name='+',
    )

    class Meta:
        abstract = True
