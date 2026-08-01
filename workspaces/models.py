"""Workspace ownership and deployment profile models."""

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


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
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
