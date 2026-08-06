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

    class MeasurementSystem(models.TextChoices):
        """Supported display measurement systems."""

        METRIC = 'metric', 'Metric'
        IMPERIAL = 'imperial', 'Imperial'

    name = models.CharField(max_length=255)
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.GARDEN,
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
        ]

    def __str__(self):
        return self.name


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
