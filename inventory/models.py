"""Inventory catalog, purchasing, and append-only stock ledger models."""

# pylint: disable=too-many-lines

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from workspaces.models import WorkspaceOwnedModel

from .ledger_validation import movement_validation_errors
from .units import (
    UnitCode,
    UnitDimension,
    convert_standard_quantity,
    get_unit_definition,
)


QUANTITY_MAX_DIGITS = 24
QUANTITY_DECIMAL_PLACES = 9
POSITIVE_DECIMAL = Decimal('0.000000001')
MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 4
COST_MAX_DIGITS = 30
COST_DECIMAL_PLACES = 12


class QuantityCertainty(models.TextChoices):
    """How confidently a stock quantity describes physical contents."""

    EXACT = 'exact', 'Exact'
    ESTIMATED = 'estimated', 'Estimated'
    UNKNOWN = 'unknown', 'Unknown'


def generate_lot_identifier():
    """Return an opaque stable lot identifier suitable for offline creation."""
    return f'LOT-{uuid4().hex.upper()}'


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
    reorder_level = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Optional low-stock threshold in the item base unit.',
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
            models.CheckConstraint(
                condition=(models.Q(reorder_level__isnull=True) | models.Q(reorder_level__gte=0)),
                name='inventory_item_nonnegative_reorder_level',
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

    def clean(self):  # pylint: disable=too-many-branches
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


class InventoryLocation(WorkspaceOwnedModel):
    """A physical or operational place that can hold stock."""

    class LocationType(models.TextChoices):
        """Controlled location roles used by stock workflows."""

        RECEIVING = 'receiving', 'Receiving'
        STORAGE = 'storage', 'Storage'
        GROWING = 'growing', 'Nursery or growing area'
        DISPATCH = 'dispatch', 'Customer dispatch'
        QUARANTINE = 'quarantine', 'Quarantine'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        SEED_PACKET = 'seed_packet', 'Seed packet'

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    location_type = models.CharField(max_length=16, choices=LocationType.choices)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='inventory_location_workspace_code_unique',
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class StockReceipt(WorkspaceOwnedModel):
    """A supplier document whose lines create exact stock lots when posted."""

    class Status(models.TextChoices):
        """Receipt lifecycle states."""

        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        REVERSED = 'reversed', 'Reversed'

    supplier = models.ForeignKey(
        'supplies.Supplier',
        on_delete=models.PROTECT,
        related_name='stock_receipts',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    received_date = models.DateField()
    supplier_reference = models.CharField(max_length=255, blank=True, default='')
    currency_code = models.CharField(
        max_length=3,
        validators=[
            RegexValidator(
                regex=r'^[A-Z]{3}$',
                message='Enter a three-letter uppercase ISO 4217 currency code.',
            ),
        ],
    )
    tax_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('0'),
        validators=(
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('100')),
        ),
    )
    tax_recoverable = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_date', '-pk']

    def __str__(self):
        return f'Receipt {self.pk or "draft"} from {self.supplier}'

    def clean(self):
        super().clean()
        if self.supplier_id and self.workspace_id != self.supplier.workspace_id:
            raise ValidationError(
                {'supplier': 'The supplier belongs to a different workspace.'},
            )

    def save(self, *args, **kwargs):
        if not self.pk and self.status != self.Status.DRAFT:
            raise ValidationError('Receipts must be created as drafts.')
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Posted receipts are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Only draft receipts can be deleted.')
        return super().delete(*args, **kwargs)


class StockReceiptLine(models.Model):
    """A draft purchase line normalized into its item's base unit."""

    receipt = models.ForeignKey(
        StockReceipt,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='receipt_lines',
    )
    supplier_lot_reference = models.CharField(
        max_length=255,
        blank=True,
        default='',
    )
    expires_on = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    quantity_certainty = models.CharField(
        max_length=16,
        choices=QuantityCertainty.choices,
        default=QuantityCertainty.EXACT,
    )
    unit_code = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        null=True,
        blank=True,
    )
    unit_conversion = models.ForeignKey(
        ItemUnitConversion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='receipt_lines',
    )
    base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    line_cost_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0'))],
    )
    destination = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name='receipt_lines',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(quantity__isnull=True) | models.Q(quantity__gt=0)),
                name='inventory_receipt_line_positive_quantity',
            ),
            models.CheckConstraint(
                condition=(models.Q(base_quantity__isnull=True) | models.Q(base_quantity__gt=0)),
                name='inventory_receipt_line_positive_base_quantity',
            ),
            models.CheckConstraint(
                condition=models.Q(line_cost_ex_tax__gte=0),
                name='inventory_receipt_line_nonnegative_cost',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.receipt_id and self.item_id:
            if self.receipt.workspace_id != self.item.workspace_id:
                errors['item'] = 'The item belongs to a different workspace.'
        if self.receipt_id and self.destination_id:
            if self.receipt.workspace_id != self.destination.workspace_id:
                errors['destination'] = 'The location belongs to a different workspace.'
        if bool(self.unit_code) == bool(self.unit_conversion_id):
            errors['unit_code'] = (
                'Select exactly one controlled unit or item conversion.'
            )
        if self.unit_conversion_id and self.unit_conversion.item_id != self.item_id:
            errors['unit_conversion'] = 'The conversion does not belong to this item.'
        if self.quantity_certainty == QuantityCertainty.UNKNOWN:
            if self.quantity is not None or self.base_quantity is not None:
                errors['quantity'] = 'Unknown quantities must not include a number.'
        elif self.quantity is None or self.base_quantity is None:
            errors['quantity'] = 'Exact and estimated quantities require a number.'
        if not errors and self.quantity is not None and self.base_quantity != self.normalized_quantity():
            errors['base_quantity'] = 'The normalized quantity is incorrect.'
        if errors:
            raise ValidationError(errors)

    def normalized_quantity(self):
        """Calculate the display quantity in the item's canonical unit."""
        if self.unit_conversion_id:
            return self.quantity * self.unit_conversion.multiplier
        return convert_standard_quantity(
            self.quantity,
            self.unit_code,
            self.item.base_unit,
        )

    def save(self, *args, **kwargs):
        if self.receipt_id and self.receipt.status != StockReceipt.Status.DRAFT:
            raise ValidationError('Posted receipt lines are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.receipt.status != StockReceipt.Status.DRAFT:
            raise ValidationError('Posted receipt lines are immutable.')
        return super().delete(*args, **kwargs)


class StockLot(WorkspaceOwnedModel):
    """An immutable exact purchase or opening-balance identity."""

    class Origin(models.TextChoices):
        """Ways a stock lot can enter the ledger."""

        RECEIPT = 'receipt', 'Supplier receipt'
        OPENING = 'opening', 'Opening balance'

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='stock_lots',
    )
    identifier = models.CharField(
        max_length=64,
        default=generate_lot_identifier,
    )
    origin = models.CharField(max_length=16, choices=Origin.choices)
    receipt_line = models.OneToOneField(
        StockReceiptLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_lot',
    )
    supplier_lot_reference = models.CharField(
        max_length=255,
        blank=True,
        default='',
    )
    received_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    initial_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    quantity_certainty = models.CharField(
        max_length=16,
        choices=QuantityCertainty.choices,
        default=QuantityCertainty.EXACT,
    )
    acquisition_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    base_unit_cost = models.DecimalField(
        max_digits=COST_MAX_DIGITS,
        decimal_places=COST_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    currency_code = models.CharField(max_length=3)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['item__name', 'identifier', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'item', 'identifier'],
                name='inventory_lot_workspace_item_identifier_unique',
            ),
            models.CheckConstraint(
                condition=(models.Q(initial_base_quantity__isnull=True) | models.Q(initial_base_quantity__gt=0)),
                name='inventory_lot_positive_initial_quantity',
            ),
            models.CheckConstraint(
                condition=(models.Q(acquisition_total__isnull=True) | models.Q(acquisition_total__gte=0)),
                name='inventory_lot_nonnegative_acquisition_total',
            ),
            models.CheckConstraint(
                condition=(models.Q(base_unit_cost__isnull=True) | models.Q(base_unit_cost__gte=0)),
                name='inventory_lot_nonnegative_unit_cost',
            ),
        ]

    def __str__(self):
        return f'{self.item}: {self.identifier}'

    def clean(self):
        super().clean()
        errors = {}
        if self.item_id and self.workspace_id != self.item.workspace_id:
            errors['item'] = 'The item belongs to a different workspace.'
        if self.receipt_line_id:
            if self.workspace_id != self.receipt_line.receipt.workspace_id:
                errors['receipt_line'] = 'The receipt line belongs to a different workspace.'
            if self.item_id != self.receipt_line.item_id:
                errors['receipt_line'] = 'The receipt line belongs to a different item.'
        if self.origin == self.Origin.RECEIPT and not self.receipt_line_id:
            errors['receipt_line'] = 'Receipt lots require receipt-line provenance.'
        if self.origin == self.Origin.OPENING and self.receipt_line_id:
            errors['receipt_line'] = 'Opening lots cannot reference a receipt line.'
        if self.quantity_certainty == QuantityCertainty.UNKNOWN:
            if self.initial_base_quantity is not None:
                errors['initial_base_quantity'] = (
                    'Unknown lots must not claim an initial quantity.'
                )
            if self.base_unit_cost is not None:
                errors['base_unit_cost'] = (
                    'Unknown lots cannot claim a per-unit cost.'
                )
        elif self.initial_base_quantity is None:
            errors['initial_base_quantity'] = (
                'Exact and estimated lots require an initial quantity.'
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Stock lots are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stock lots cannot be deleted.')


class Stocktake(WorkspaceOwnedModel):
    """A counted stock document that posts explicit variance movements."""

    class Status(models.TextChoices):
        """Stocktake lifecycle states."""

        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        REVERSED = 'reversed', 'Reversed'

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    counted_at = models.DateTimeField()
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-counted_at', '-pk']

    def __str__(self):
        return f'Stocktake {self.pk or "draft"}'

    def save(self, *args, **kwargs):
        if not self.pk and self.status != self.Status.DRAFT:
            raise ValidationError('Stocktakes must be created as drafts.')
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Posted stocktakes are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError('Only draft stocktakes can be deleted.')
        return super().delete(*args, **kwargs)


class StocktakeLine(models.Model):
    """One exact lot/location count and its posted variance snapshot."""

    stocktake = models.ForeignKey(
        Stocktake,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='stocktake_lines',
    )
    location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name='stocktake_lines',
    )
    counted_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0'))],
    )
    unit_code = models.CharField(
        max_length=16,
        choices=UnitCode.choices,
        null=True,
        blank=True,
    )
    unit_conversion = models.ForeignKey(
        ItemUnitConversion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stocktake_lines',
    )
    counted_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0'))],
    )
    expected_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    variance_base_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    reason = models.TextField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['stocktake', 'lot', 'location'],
                name='inventory_stocktake_lot_location_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(counted_quantity__gte=0),
                name='inventory_stocktake_nonnegative_counted_quantity',
            ),
            models.CheckConstraint(
                condition=models.Q(counted_base_quantity__gte=0),
                name='inventory_stocktake_nonnegative_base_quantity',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.stocktake_id and self.lot_id:
            if self.stocktake.workspace_id != self.lot.workspace_id:
                errors['lot'] = 'The lot belongs to a different workspace.'
        if self.stocktake_id and self.location_id:
            if self.stocktake.workspace_id != self.location.workspace_id:
                errors['location'] = 'The location belongs to a different workspace.'
        if bool(self.unit_code) == bool(self.unit_conversion_id):
            errors['unit_code'] = (
                'Select exactly one controlled unit or item conversion.'
            )
        if self.unit_conversion_id and self.lot_id:
            if self.unit_conversion.item_id != self.lot.item_id:
                errors['unit_conversion'] = 'The conversion does not belong to the lot item.'
        if not errors and self.counted_base_quantity != self.normalized_quantity():
            errors['counted_base_quantity'] = 'The normalized count is incorrect.'
        if errors:
            raise ValidationError(errors)

    def normalized_quantity(self):
        """Calculate the count in the lot item's canonical unit."""
        if self.unit_conversion_id:
            return self.counted_quantity * self.unit_conversion.multiplier
        return convert_standard_quantity(
            self.counted_quantity,
            self.unit_code,
            self.lot.item.base_unit,
        )

    def save(self, *args, **kwargs):
        if self.stocktake_id and self.stocktake.status != Stocktake.Status.DRAFT:
            raise ValidationError('Posted stocktake lines are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.stocktake.status != Stocktake.Status.DRAFT:
            raise ValidationError('Posted stocktake lines are immutable.')
        return super().delete(*args, **kwargs)


class StockMovement(WorkspaceOwnedModel):
    """One immutable positive-quantity entry in the physical stock ledger."""

    class MovementType(models.TextChoices):
        """Supported inventory events."""

        OPENING = 'opening', 'Opening balance'
        RECEIPT = 'receipt', 'Receipt'
        CONSUMPTION = 'consumption', 'Consumption'
        TRANSFER = 'transfer', 'Transfer'
        ADJUSTMENT_GAIN = 'adjustment_gain', 'Adjustment gain'
        ADJUSTMENT_LOSS = 'adjustment_loss', 'Adjustment loss'
        WASTE = 'waste', 'Waste'
        SALE = 'sale', 'Sale'
        CUSTOMER_RETURN = 'customer_return', 'Customer return'
        REVERSAL = 'reversal', 'Reversal'

    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='movements',
    )
    movement_type = models.CharField(max_length=24, choices=MovementType.choices)
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    source = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='outgoing_stock_movements',
    )
    destination = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='incoming_stock_movements',
    )
    occurred_at = models.DateTimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    reason = models.TextField(blank=True, default='')
    reference = models.CharField(max_length=255, blank=True, default='')
    reversal_of = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversal',
    )
    receipt_line = models.ForeignKey(
        StockReceiptLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movements',
    )
    stocktake_line = models.ForeignKey(
        StocktakeLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movements',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_at', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='inventory_movement_positive_quantity',
            ),
            models.CheckConstraint(
                condition=(models.Q(source__isnull=True) | models.Q(destination__isnull=True) | ~models.Q(source=models.F('destination'))),
                name='inventory_movement_distinct_locations',
            ),
        ]

    def __str__(self):
        return f'{self.movement_type} {self.quantity} {self.lot.item.base_unit}'

    def clean(self):
        super().clean()
        errors = self._workspace_errors()
        errors.update(movement_validation_errors(self))
        if errors:
            raise ValidationError(errors)

    def _workspace_errors(self):
        related = {
            'lot': self.lot if self.lot_id else None,
            'source': self.source if self.source_id else None,
            'destination': self.destination if self.destination_id else None,
        }
        errors = {
            field: f'The {field} belongs to a different workspace.'
            for field, value in related.items()
            if value is not None and value.workspace_id != self.workspace_id
        }
        if self.receipt_line_id:
            if self.receipt_line.receipt.workspace_id != self.workspace_id:
                errors['receipt_line'] = 'The receipt line belongs to a different workspace.'
        if self.stocktake_line_id:
            if self.stocktake_line.stocktake.workspace_id != self.workspace_id:
                errors['stocktake_line'] = 'The stocktake line belongs to a different workspace.'
        return errors

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Stock movements are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stock movements cannot be deleted.')
