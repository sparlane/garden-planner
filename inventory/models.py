"""Inventory catalog and item-specific measurement models."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from workspaces.models import WorkspaceOwnedModel

from .units import UnitCode, UnitDimension, get_unit_definition


QUANTITY_MAX_DIGITS = 24
QUANTITY_DECIMAL_PLACES = 9
POSITIVE_DECIMAL = Decimal('0.000000001')


class InventoryItem(WorkspaceOwnedModel):
    """One workspace-owned definition of a physical stock item."""

    class Category(models.TextChoices):
        """Supported physical-input categories."""

        SEED = 'seed', 'Seed'
        GROWING_MEDIA = 'growing_media', 'Growing media'
        FERTILIZER_TREATMENT = (
            'fertilizer_treatment',
            'Fertilizer or treatment',
        )
        LABEL = 'label', 'Label'
        PACKAGING = 'packaging', 'Packaging'
        POT_CONTAINER = 'pot_container', 'Pot or container'
        TRAY = 'tray', 'Tray'
        OTHER = 'other', 'Other physical input'

    class TrackingMode(models.TextChoices):
        """Supported stock identity strategies."""

        LOT = 'lot', 'Lot controlled'
        SERIALIZED = 'serialized', 'Serialized'

    class UsageBasis(models.TextChoices):
        """Ways task 42 may calculate suggested consumption."""

        CELL_VOLUME = 'cell_volume', 'Cell volume'
        SURFACE_AREA = 'surface_area', 'Surface-area rate'
        PER_UNIT = 'per_unit', 'Per plant or item'
        FIXED = 'fixed', 'Fixed quantity'
        MANUAL = 'manual', 'Manual'

    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64, blank=True, default='')
    category = models.CharField(max_length=32, choices=Category.choices)
    description = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    base_unit = models.CharField(max_length=16, choices=UnitCode.choices)
    tracking_mode = models.CharField(
        max_length=16,
        choices=TrackingMode.choices,
        default=TrackingMode.LOT,
    )
    default_usage_basis = models.CharField(
        max_length=16,
        choices=UsageBasis.choices,
        default=UsageBasis.MANUAL,
    )
    default_usage_rate = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
        help_text='Base-unit quantity consumed per usage-rate unit.',
    )
    usage_rate_unit = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        null=True,
        blank=True,
    )
    default_fixed_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
        help_text='Base-unit quantity suggested for fixed usage.',
    )
    stock_history_started_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'sku'],
                condition=~models.Q(sku=''),
                name='inventory_item_workspace_sku_unique',
            ),
            models.CheckConstraint(
                condition=(models.Q(default_usage_rate__isnull=True) | models.Q(default_usage_rate__gt=0)),
                name='inventory_item_positive_usage_rate',
            ),
            models.CheckConstraint(
                condition=(models.Q(default_fixed_quantity__isnull=True) | models.Q(default_fixed_quantity__gt=0)),
                name='inventory_item_positive_fixed_quantity',
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def default_tracking_mode(cls, category):
        """Return the creation default for an item category."""
        if category == cls.Category.TRAY:
            return cls.TrackingMode.SERIALIZED
        return cls.TrackingMode.LOT

    def clean(self):
        """Validate unit semantics and usage configuration as one whole."""
        super().clean()
        errors = {}

        base_unit = self._validate_base_unit(errors)
        if base_unit is not None:
            self._validate_item_unit_semantics(errors)
        self._validate_usage_configuration(errors)

        if errors:
            raise ValidationError(errors)

    def _validate_base_unit(self, errors):
        """Resolve the base unit while retaining the registry's exact error."""
        try:
            return get_unit_definition(self.base_unit)
        except ValidationError as exc:
            errors['base_unit'] = exc.messages
            return None

    def _validate_item_unit_semantics(self, errors):
        """Match semantic count units to seed and serialized identities."""
        if self.category == self.Category.SEED and self.base_unit not in {
            UnitCode.SEED,
            UnitCode.SEED_CLUSTER,
        }:
            errors['base_unit'] = 'Seed items use the seed or seed_cluster unit.'
        if self.tracking_mode == self.TrackingMode.SERIALIZED and self.base_unit != UnitCode.EACH:
            errors['base_unit'] = 'Serialized items must use each as their base unit.'

    def _validate_usage_configuration(self, errors):
        """Dispatch the selected usage basis to its configuration rules."""
        validators = {
            self.UsageBasis.CELL_VOLUME: self._validate_rate_based_usage,
            self.UsageBasis.SURFACE_AREA: self._validate_rate_based_usage,
            self.UsageBasis.PER_UNIT: self._validate_rate_based_usage,
            self.UsageBasis.FIXED: self._validate_fixed_usage,
            self.UsageBasis.MANUAL: self._validate_manual_usage,
        }
        validator = validators.get(self.default_usage_basis)
        if validator:
            validator(errors)

    def _validate_rate_based_usage(self, errors):
        """Require a positive rate with the correct denominator dimension."""
        rate_dimensions = {
            self.UsageBasis.CELL_VOLUME: UnitDimension.VOLUME,
            self.UsageBasis.SURFACE_AREA: UnitDimension.AREA,
            self.UsageBasis.PER_UNIT: UnitDimension.COUNT,
        }
        if self.default_usage_rate is None:
            errors['default_usage_rate'] = 'This usage basis requires a rate.'
        if not self.usage_rate_unit:
            errors['usage_rate_unit'] = 'This usage basis requires a rate unit.'
        else:
            try:
                rate_unit = get_unit_definition(self.usage_rate_unit)
            except ValidationError as exc:
                errors['usage_rate_unit'] = exc.messages
            else:
                required_dimension = rate_dimensions[self.default_usage_basis]
                if rate_unit.dimension != required_dimension:
                    errors['usage_rate_unit'] = (
                        'The rate unit has an incompatible dimension.'
                    )
        if self.default_fixed_quantity is not None:
            errors['default_fixed_quantity'] = (
                'Rate-based usage cannot also define a fixed quantity.'
            )

    def _validate_fixed_usage(self, errors):
        """Allow one fixed base-unit quantity without rate fields."""
        if self.default_fixed_quantity is None:
            errors['default_fixed_quantity'] = (
                'Fixed usage requires a default quantity.'
            )
        if self.default_usage_rate is not None:
            errors['default_usage_rate'] = 'Fixed usage does not accept a rate.'
        if self.usage_rate_unit:
            errors['usage_rate_unit'] = 'Fixed usage does not accept a rate unit.'

    def _validate_manual_usage(self, errors):
        """Keep manual usage free of an implied quantity or formula."""
        if self.default_usage_rate is not None:
            errors['default_usage_rate'] = 'Manual usage does not accept a rate.'
        if self.usage_rate_unit:
            errors['usage_rate_unit'] = 'Manual usage does not accept a rate unit.'
        if self.default_fixed_quantity is not None:
            errors['default_fixed_quantity'] = (
                'Manual usage does not accept a fixed quantity.'
            )

    def _identity_lock_errors(self, previous):
        """Return changes forbidden after the first stock movement."""
        locked_fields = {
            'base_unit': (previous.base_unit, self.base_unit),
            'tracking_mode': (
                previous.tracking_mode,
                self.tracking_mode,
            ),
        }
        errors = {
            field: 'Create a new item instead of changing this after stock history exists.'
            for field, values in locked_fields.items()
            if values[0] != values[1]
        }
        if previous.stock_history_started_at != self.stock_history_started_at:
            errors['stock_history_started_at'] = (
                'Stock-history state cannot be changed.'
            )
        return errors

    def save(self, *args, **kwargs):
        """Enforce configuration validity and post-history immutability."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.stock_history_started_at:
                errors = self._identity_lock_errors(previous)
                if errors:
                    raise ValidationError(errors)
        self.full_clean()
        super().save(*args, **kwargs)

    def mark_stock_history_started(self, occurred_at=None):
        """Idempotently lock identity fields when the first movement posts."""
        timestamp = occurred_at or timezone.now()
        type(self).objects.filter(
            pk=self.pk,
            stock_history_started_at__isnull=True,
        ).update(stock_history_started_at=timestamp)
        self.refresh_from_db(fields=['stock_history_started_at'])

    def delete(self, *args, **kwargs):
        """Require catalog deactivation so historical identities survive."""
        raise ValidationError('Inventory items must be deactivated, not deleted.')


class ItemUnitConversion(WorkspaceOwnedModel):
    """An item-specific package or application unit."""

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='unit_conversions',
    )
    label = models.CharField(max_length=128)
    multiplier = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
        help_text='Quantity of the item base unit represented by one package unit.',
    )
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['label', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['item', 'label'],
                name='inventory_conversion_item_label_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(multiplier__gt=0),
                name='inventory_conversion_positive_multiplier',
            ),
        ]

    def __str__(self):
        return f'{self.label} of {self.item}'

    def clean(self):
        """Keep package units within their item's workspace."""
        super().clean()
        if self.item_id and self.workspace_id != self.item.workspace_id:
            raise ValidationError(
                {'item': 'The item belongs to a different workspace.'},
            )

    def save(self, *args, **kwargs):
        """Validate direct ORM writes as well as REST writes."""
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Require deactivation so historical package labels survive."""
        raise ValidationError(
            'Item unit conversions must be deactivated, not deleted.',
        )
