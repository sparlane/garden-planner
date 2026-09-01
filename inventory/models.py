"""Inventory catalog, purchasing, and append-only stock ledger models."""

# pylint: disable=too-many-lines

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from locations.models import Location
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


def generate_asset_code():
    """Return an opaque, server-owned serialized asset identity."""
    return f'ASSET-{uuid4().hex.upper()}'


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
        """Supported stock identity strategies.

        `MIXED` is lot-controlled stock that can also be individually
        numbered a few units at a time, for a durable item bought as a box
        of identical anonymous ones. The bulk remainder stays anonymous and
        is derived, never stored: see `inventory.ledger.bulk_balance`.
        """

        LOT = 'lot', 'Lot controlled'
        SERIALIZED = 'serialized', 'Serialized'
        MIXED = 'mixed', 'Lot controlled, individually numberable'

    #: Modes whose stock can carry `InventoryUnit` identities. Serialized
    #: items are nothing but units; mixed items hold both, so asking this
    #: rather than comparing against one mode is what keeps the two apart.
    INDIVIDUALLY_IDENTIFIED = frozenset({
        TrackingMode.SERIALIZED,
        TrackingMode.MIXED,
    })

    class UsageBasis(models.TextChoices):
        """Ways input applications may calculate suggested consumption."""

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
    container_size_label = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='Commercial container size, for example P9 or 2 L.',
    )
    container_volume_ml = models.PositiveIntegerField(null=True, blank=True)
    container_footprint_m2 = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
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

    def clean(self):
        """Validate unit semantics and usage configuration as one whole."""
        super().clean()
        errors = {}

        base_unit = self._validate_base_unit(errors)
        if base_unit is not None:
            self._validate_item_unit_semantics(errors)
        self._validate_usage_configuration(errors)
        self._validate_container_configuration(errors)

        if errors:
            raise ValidationError(errors)

    def _validate_container_configuration(self, errors):
        """Keep physical container metadata on container catalog items only."""
        configured = any((
            self.container_size_label,
            self.container_volume_ml is not None,
            self.container_footprint_m2 is not None,
        ))
        if configured and self.category != self.Category.POT_CONTAINER:
            errors['container_size_label'] = (
                'Container metadata is only valid for pot or container items.'
            )

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
        # Both identity modes count whole things, so both are held to `each`.
        # Mixed stock is numbered one unit at a time; a fraction of a litre
        # cannot be given an asset code.
        if self.tracking_mode in self.INDIVIDUALLY_IDENTIFIED and self.base_unit != UnitCode.EACH:
            errors['base_unit'] = 'Individually identified items must use each as their base unit.'

    def _validate_usage_configuration(self, errors):
        """Dispatch the selected usage basis to its configuration rules."""
        validators = {
            self.UsageBasis.SURFACE_AREA: self._validate_rate_based_usage,
            self.UsageBasis.PER_UNIT: self._validate_rate_based_usage,
            self.UsageBasis.FIXED: self._validate_fixed_usage,
            self.UsageBasis.MANUAL: self._validate_manual_usage,
        }
        if self.default_usage_basis == self.UsageBasis.CELL_VOLUME:
            self._validate_cell_volume_usage(errors)
            return
        validator = validators.get(self.default_usage_basis)
        if validator:
            validator(errors)

    def _validate_cell_volume_usage(self, errors):
        """Derive usage directly from tray cells in the item's volume unit."""
        try:
            base_unit = get_unit_definition(self.base_unit)
        except ValidationError:
            return
        if base_unit.dimension != UnitDimension.VOLUME:
            errors['base_unit'] = (
                'Cell-volume usage requires a volume base unit.'
            )
        if self.default_usage_rate is not None:
            errors['default_usage_rate'] = (
                'Cell-volume usage derives quantity from each tray cell.'
            )
        if self.usage_rate_unit:
            errors['usage_rate_unit'] = (
                'Cell-volume usage derives quantity from each tray cell.'
            )
        if self.default_fixed_quantity is not None:
            errors['default_fixed_quantity'] = (
                'Cell-volume usage does not accept a fixed quantity.'
            )

    def _validate_rate_based_usage(self, errors):
        """Require a positive rate with the correct denominator dimension."""
        rate_dimensions = {
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

    @classmethod
    def widens_tracking_mode(cls, previous_mode, new_mode):
        """Return whether this is the one identity change stock history allows.

        Lot to mixed only adds the ability to number a few units later. Every
        existing lot, movement and balance keeps the meaning it already had,
        and the bulk remainder is still the whole lot until something is
        numbered. Without this an established nursery would have to retire its
        pot items and re-receive stock to use the feature at all.

        The REST serializer repeats this check, so it lives here rather than
        in either caller.
        """
        was_lot = previous_mode == cls.TrackingMode.LOT
        now_mixed = new_mode == cls.TrackingMode.MIXED
        return was_lot and now_mixed

    def _identity_lock_errors(self, previous):
        """Return changes forbidden after the first stock movement."""
        locked_fields = {
            'base_unit': (previous.base_unit, self.base_unit),
            'tracking_mode': (
                previous.tracking_mode,
                self.tracking_mode,
            ),
        }
        if self.widens_tracking_mode(previous.tracking_mode, self.tracking_mode):
            del locked_fields['tracking_mode']
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


class StockReceipt(WorkspaceOwnedModel):
    """A supplier document whose lines create exact stock lots when posted."""

    class Status(models.TextChoices):
        """Receipt lifecycle states."""

        DRAFT = 'draft', 'Draft'
        POSTED = 'posted', 'Posted'
        REVERSED = 'reversed', 'Reversed'

    class SourceDocumentType(models.TextChoices):
        """The principal record supporting the supplier purchase."""

        NONE = 'none', 'No source document recorded'
        TAXABLE_SUPPLY = 'taxable_supply', 'Taxable supply information'
        INVOICE = 'invoice', 'Invoice'
        RECEIPT = 'receipt', 'Receipt'
        BUYER_CREATED = 'buyer_created', 'Buyer-created taxable supply information'
        CUSTOMS_ENTRY = 'customs_entry', 'Customs entry or statement'
        CONTRACT = 'contract', 'Contract or supplier agreement'
        BANK_RECORD = 'bank_record', 'Bank or payment record'
        OTHER = 'other', 'Other record'

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
    invoice_date = models.DateField(null=True, blank=True)
    source_document_type = models.CharField(
        max_length=24,
        choices=SourceDocumentType.choices,
        default=SourceDocumentType.NONE,
    )
    source_document_number = models.CharField(max_length=255, blank=True, default='')
    evidence_reference = models.CharField(max_length=255, blank=True, default='')
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    supplier_name_snapshot = models.CharField(max_length=1024, blank=True, default='')
    supplier_address_snapshot = models.TextField(blank=True, default='')
    supplier_gst_status = models.CharField(
        max_length=16,
        choices=(
            ('registered', 'GST registered'),
            ('unregistered', 'Not GST registered'),
            ('unknown', 'Unknown'),
        ),
        default='unknown',
    )
    supplier_gst_number = models.CharField(max_length=16, blank=True, default='')
    currency_code = models.CharField(
        max_length=3,
        validators=[
            RegexValidator(
                regex=r'^[A-Z]{3}$',
                message='Enter a three-letter uppercase ISO 4217 currency code.',
            ),
        ],
    )
    #: The date the supplier was paid. Under the payments and hybrid bases this
    #: is when input tax on the receipt falls due, so it has to be recordable
    #: after posting — a supplier is paid after the goods arrive, not before
    #: the document is closed. `settle_receipt` is the only writer, and it goes
    #: through the queryset because `save` refuses every post-posting change.
    settled_on = models.DateField(null=True, blank=True, editable=False)
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


class StockReceiptLine(models.Model):  # pylint: disable=too-many-instance-attributes
    """A draft purchase line normalized into its item's base unit."""

    class TaxTreatment(models.TextChoices):
        """How the purchased supply is treated for GST."""

        STANDARD = 'standard', 'Standard-rated'
        ZERO_RATED = 'zero_rated', 'Zero-rated'
        EXEMPT = 'exempt', 'Exempt'
        OUT_OF_SCOPE = 'out_of_scope', 'Outside the scope of GST'
        UNKNOWN = 'unknown', 'Unknown'

    class InputTaxSource(models.TextChoices):
        """Where an input-tax amount comes from."""

        NONE = 'none', 'No input tax'
        SUPPLIER = 'supplier', 'GST charged by supplier'
        CUSTOMS = 'customs', 'GST levied by Customs'
        SECOND_HAND = 'second_hand', 'Second-hand-goods deduction'

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
    supplier_cost_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    tax_treatment = models.CharField(
        max_length=16,
        choices=TaxTreatment.choices,
        default=TaxTreatment.UNKNOWN,
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
    input_tax_source = models.CharField(
        max_length=16,
        choices=InputTaxSource.choices,
        default=InputTaxSource.NONE,
    )
    input_tax_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    claim_input_tax = models.BooleanField(default=False)
    claimable_percentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('0'),
        validators=(
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('100')),
        ),
    )
    apportionment_basis = models.TextField(blank=True, default='')
    recoverable_input_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal('0'),
        editable=False,
        validators=[MinValueValidator(Decimal('0'))],
    )
    non_recoverable_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal('0'),
        editable=False,
        validators=[MinValueValidator(Decimal('0'))],
    )
    acquisition_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal('0'),
        editable=False,
        validators=[MinValueValidator(Decimal('0'))],
    )
    legacy_tax_classification = models.BooleanField(default=False, editable=False)
    destination = models.ForeignKey(
        Location,
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
        errors.update(self._tax_errors())
        if errors:
            raise ValidationError(errors)

    def _tax_errors(self):
        """Return structural tax errors without deciding legal eligibility."""
        errors = {}
        rate = Decimal(self.tax_rate or 0)
        tax = Decimal(self.input_tax_amount or 0)
        percentage = Decimal(self.claimable_percentage or 0)
        if self.tax_treatment == self.TaxTreatment.STANDARD and rate <= 0:
            errors['tax_rate'] = 'A standard-rated line needs a positive tax rate.'
        if self.tax_treatment != self.TaxTreatment.STANDARD and rate > 0:
            errors['tax_rate'] = 'Only a standard-rated line carries a tax rate.'
        if self.input_tax_source == self.InputTaxSource.NONE and tax != 0:
            errors['input_tax_amount'] = 'Select where this input tax came from.'
        if self.input_tax_source != self.InputTaxSource.NONE and tax <= 0:
            errors['input_tax_amount'] = 'This input-tax source needs a positive amount.'
        if self.claim_input_tax and percentage <= 0:
            errors['claimable_percentage'] = 'A claim needs a positive percentage.'
        if not self.claim_input_tax and percentage != 0:
            errors['claimable_percentage'] = 'An unclaimed line must use zero percent.'
        if 0 < percentage < 100 and not self.apportionment_basis.strip():
            errors['apportionment_basis'] = 'Explain how a partial claim was apportioned.'
        return errors

    def _set_tax_amounts(self):
        """Freeze the claim split and acquisition amount from explicit inputs."""
        quantum = Decimal('0.0001')
        tax = Decimal(self.input_tax_amount or 0).quantize(quantum)
        percentage = Decimal(self.claimable_percentage or 0)
        recoverable = Decimal('0')
        if self.claim_input_tax:
            recoverable = (tax * percentage / Decimal('100')).quantize(quantum)
        self.recoverable_input_tax = recoverable
        self.non_recoverable_tax = tax - recoverable
        gross = Decimal(self.supplier_cost_incl_tax or 0).quantize(quantum)
        if gross == 0 and tax == 0 and Decimal(self.line_cost_ex_tax or 0) > 0:
            gross = Decimal(self.line_cost_ex_tax).quantize(quantum)
            self.supplier_cost_incl_tax = gross
        if self.input_tax_source == self.InputTaxSource.CUSTOMS:
            self.acquisition_amount = gross + self.non_recoverable_tax
            self.line_cost_ex_tax = gross
        else:
            self.acquisition_amount = gross - recoverable
            supplier_tax = tax if self.input_tax_source in {
                self.InputTaxSource.SUPPLIER,
                self.InputTaxSource.SECOND_HAND,
            } else Decimal('0')
            self.line_cost_ex_tax = gross - supplier_tax

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
        self._set_tax_amounts()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.receipt.status != StockReceipt.Status.DRAFT:
            raise ValidationError('Posted receipt lines are immutable.')
        return super().delete(*args, **kwargs)


class InputTaxAdjustment(WorkspaceOwnedModel):
    """An immutable later change to the input tax claimed for a receipt line."""

    receipt_line = models.ForeignKey(
        StockReceiptLine,
        on_delete=models.PROTECT,
        related_name='input_tax_adjustments',
    )
    adjustment_date = models.DateField()
    previous_claimable_percentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=(MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))),
    )
    revised_claimable_percentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=(MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))),
    )
    tax_adjustment = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        help_text='Positive increases the deduction; negative reduces it.',
    )
    apportionment_basis = models.TextField()
    reason = models.TextField()
    evidence_reference = models.CharField(max_length=255, blank=True, default='')
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['adjustment_date', 'pk']

    def clean(self):
        """Require a posted same-workspace line and exact percentage delta."""
        super().clean()
        errors = {}
        if self.receipt_line_id:
            receipt = StockReceipt.objects.only('workspace_id', 'status').get(
                pk=self.receipt_line.receipt_id,
            )
            if receipt.workspace_id != self.workspace_id:
                errors['receipt_line'] = 'The receipt line belongs to another workspace.'
            if receipt.status != StockReceipt.Status.POSTED:
                errors['receipt_line'] = 'Adjust input tax only on a posted receipt.'
            revised = Decimal(self.revised_claimable_percentage)
            previous = Decimal(self.previous_claimable_percentage)
            percentage_delta = revised - previous
            tax_delta = Decimal(self.receipt_line.input_tax_amount) * percentage_delta
            expected = (tax_delta / Decimal('100')).quantize(Decimal('0.0001'))
            if Decimal(self.tax_adjustment) != expected:
                errors['tax_adjustment'] = f'The percentage change produces {expected:.4f}.'
        if not self.reason.strip():
            errors['reason'] = 'Explain why the taxable use changed.'
        if not self.apportionment_basis.strip():
            errors['apportionment_basis'] = 'Record the revised apportionment basis.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Create once; later corrections append another adjustment."""
        if self.pk:
            raise ValidationError('Input-tax adjustments are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Input-tax adjustments are immutable.')


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
        indexes = [
            models.Index(fields=['workspace', 'expires_on'], name='stock_lot_expiry_idx'),
        ]
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


class InventoryUnit(WorkspaceOwnedModel):
    """One individually identified unit of a serialized inventory item."""

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='serialized_units',
    )
    source_lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='serialized_units',
    )
    asset_code = models.CharField(max_length=64, default=generate_asset_code)
    acquisition_cost = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    currency_code = models.CharField(max_length=3)
    current_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='serialized_units',
    )
    active = models.BooleanField(default=True)
    # Numbering part of a mixed lot posts no movement, so without this there
    # would be no record at all of who gave these pots their identities. A
    # unit received on a serialized receipt has its receipt to say that, and
    # leaves this blank.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_code', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'asset_code'],
                name='inventory_unit_workspace_asset_code_unique',
            ),
            models.CheckConstraint(
                condition=(models.Q(acquisition_cost__isnull=True) | models.Q(acquisition_cost__gte=0)),
                name='inventory_unit_nonnegative_acquisition_cost',
            ),
        ]

    def __str__(self):
        return self.asset_code

    def clean(self):
        """Keep unit identity within one serialized item and workspace."""
        super().clean()
        errors = {}
        if self.item_id:
            if self.item.workspace_id != self.workspace_id:
                errors['item'] = 'The item belongs to a different workspace.'
            if self.item.tracking_mode not in InventoryItem.INDIVIDUALLY_IDENTIFIED:
                errors['item'] = 'Inventory units require a serialized or mixed item.'
            if self.item.base_unit != UnitCode.EACH:
                errors['item'] = 'Serialized items must use each as their base unit.'
        if self.source_lot_id:
            if self.source_lot.workspace_id != self.workspace_id:
                errors['source_lot'] = 'The source lot belongs to a different workspace.'
            if self.item_id and self.source_lot.item_id != self.item_id:
                errors['source_lot'] = 'The source lot belongs to a different item.'
        if self.current_location_id:
            if self.current_location.workspace_id != self.workspace_id:
                errors['current_location'] = 'The location belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Validate direct writes and lock provenance after creation."""
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and self.movements.exists():
                locked = ('workspace_id', 'item_id', 'source_lot_id', 'asset_code')
                errors = {
                    field.removesuffix('_id'): 'Serialized-unit identity is immutable after stock history exists.'
                    for field in locked
                    if getattr(previous, field) != getattr(self, field)
                }
                if errors:
                    raise ValidationError(errors)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Refuse deletion; identities outlive the thing they identified.

        `inventory.ledger.discard_numbering` is the one sanctioned exception,
        for a unit numbered by mistake and never used for anything. It deletes
        through the queryset, the same way `_sync_unit_after_movement` already
        writes through one, so this guard stays absolute for ordinary code.
        """
        raise ValidationError('Serialized inventory units cannot be deleted.')


class InventoryUnitReconciliation(WorkspaceOwnedModel):
    """One immutable opening-cost and location audit for a legacy unit."""

    unit = models.OneToOneField(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name='opening_reconciliation',
    )
    acquisition_cost = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0'))],
    )
    movement = models.OneToOneField(
        'StockMovement',
        on_delete=models.PROTECT,
        related_name='unit_opening_reconciliation',
    )
    reason = models.TextField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']

    def clean(self):
        """Keep reconciliation evidence with its unit and workspace."""
        super().clean()
        errors = {}
        if self.unit_id and self.unit.workspace_id != self.workspace_id:
            errors['unit'] = 'The unit belongs to a different workspace.'
        if self.movement_id and self.movement.workspace_id != self.workspace_id:
            errors['movement'] = 'The movement belongs to a different workspace.'
        if not self.reason.strip():
            errors['reason'] = 'A reason is required.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Unit reconciliations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Unit reconciliations cannot be deleted.')


class Stocktake(WorkspaceOwnedModel):
    """A counted stock document that posts explicit variance movements."""

    class Status(models.TextChoices):
        """Stocktake lifecycle states."""

        DRAFT = 'draft', 'Draft'
        OPEN = 'open', 'Open'
        PAUSED = 'paused', 'Paused'
        REVIEW = 'review', 'In review'
        APPROVED = 'approved', 'Approved'
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
    blind = models.BooleanField(default=True)
    scope = models.JSONField(default=dict, blank=True)
    scope_digest = models.CharField(max_length=64, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-counted_at', '-pk']

    def __str__(self):
        return f'Stocktake {self.pk or "draft"}'

    def save(self, *args, **kwargs):
        if not self.pk and self.status not in {self.Status.DRAFT, self.Status.OPEN}:
            raise ValidationError('Stocktakes must be created as drafts or open sessions.')
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status != self.Status.DRAFT:
                raise ValidationError('Posted stocktakes are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status not in {self.Status.DRAFT, self.Status.OPEN, self.Status.PAUSED}:
            raise ValidationError('Only unreviewed stocktakes can be deleted.')
        return super().delete(*args, **kwargs)


class StocktakeTarget(models.Model):
    """One frozen quantity or identity expected inside a stocktake scope."""

    class TargetType(models.TextChoices):
        """Physical domains that participate in a mixed stocktake."""

        LOT = 'lot', 'Consumable lot'
        SEED_PACKET = 'seed_packet', 'Seed packet'
        TRAY = 'tray', 'Serialized tray'
        UNIT = 'unit', 'Numbered unit'
        COHORT = 'cohort', 'Plant cohort'
        PLANT = 'plant', 'Individual plant'

    class CountStatus(models.TextChoices):
        """Whether this frozen target still needs physical evidence."""

        PENDING = 'pending', 'Pending'
        COUNTED = 'counted', 'Counted'
        RECOUNT = 'recount', 'Recount requested'

    stocktake = models.ForeignKey(
        Stocktake, on_delete=models.CASCADE, related_name='targets',
    )
    target_type = models.CharField(max_length=16, choices=TargetType.choices)
    target_key = models.CharField(max_length=96)
    target_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    display = models.CharField(max_length=255)
    expected_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    expected_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    expected_state = models.CharField(max_length=32, blank=True, default='')
    expected_snapshot = models.JSONField(default=dict)
    source_revision = models.CharField(max_length=64)
    review_revision = models.CharField(max_length=64, blank=True, default='')
    review_snapshot = models.JSONField(default=dict, blank=True)
    unexpected = models.BooleanField(default=False)
    count_status = models.CharField(
        max_length=16, choices=CountStatus.choices, default=CountStatus.PENDING,
    )
    accepted_count = models.OneToOneField(
        'StocktakeCount', on_delete=models.PROTECT, null=True, blank=True,
        related_name='accepted_for',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['target_type', 'display', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['stocktake', 'target_key'],
                name='inventory_stocktake_target_key_unique',
            ),
        ]


class StocktakeCount(models.Model):
    """An immutable blind count attempt retained across recounts."""

    target = models.ForeignKey(
        StocktakeTarget, on_delete=models.PROTECT, related_name='counts',
    )
    counted_quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    observed_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    observed_state = models.CharField(max_length=32, blank=True, default='')
    code_snapshot = models.CharField(max_length=64, blank=True, default='')
    resolved_identity = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default='')
    counter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Stocktake counts are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stocktake counts cannot be deleted.')


class StocktakeVariance(models.Model):
    """A reviewable difference between frozen scope and physical evidence."""

    class Kind(models.TextChoices):
        """Review classifications shared by quantity and identity counts."""

        QUANTITY = 'quantity', 'Quantity'
        MISSING = 'missing', 'Missing'
        EXCESS = 'excess', 'Excess'
        MISPLACED = 'misplaced', 'Misplaced'
        STATE = 'state_mismatch', 'State mismatch'

    class ConflictResolution(models.TextChoices):
        """Explicit decisions for facts that changed after the snapshot."""

        NONE = '', 'No conflict'
        ACCEPTED = 'accepted', 'Accepted current conflict'
        REFRESHED = 'refreshed', 'Refreshed snapshot'
        RECOUNT = 'recount', 'Recount requested'

    target = models.ForeignKey(
        StocktakeTarget, on_delete=models.PROTECT, related_name='variances',
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    expected = models.JSONField(default=dict)
    observed = models.JSONField(default=dict)
    source_changed = models.BooleanField(default=False)
    current_revision = models.CharField(max_length=64, blank=True, default='')
    conflict_resolution = models.CharField(
        max_length=12, choices=ConflictResolution.choices, blank=True, default='',
    )
    conflict_reason = models.TextField(blank=True, default='')
    resolution_action = models.CharField(max_length=32, blank=True, default='')
    resolution_payload = models.JSONField(default=dict, blank=True)
    resolution_reason = models.TextField(blank=True, default='')
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['target_id', 'kind', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['target', 'kind'],
                name='inventory_stocktake_variance_kind_unique',
            ),
        ]


class StocktakeAttachment(models.Model):
    """An externally hosted photo or document retained with count evidence."""

    stocktake = models.ForeignKey(
        Stocktake, on_delete=models.PROTECT, related_name='attachments',
    )
    target = models.ForeignKey(
        StocktakeTarget, on_delete=models.PROTECT, null=True, blank=True,
        related_name='attachments',
    )
    url = models.URLField(max_length=2048)
    label = models.CharField(max_length=255, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['stocktake', 'url'],
                name='inventory_stocktake_attachment_url_unique',
            ),
        ]


class StocktakeReconciliation(models.Model):
    """An immutable link to one authoritative correction or reversal."""

    class Phase(models.TextChoices):
        """Whether the result applies or compensates for a stocktake."""

        POST = 'post', 'Posted correction'
        REVERSE = 'reverse', 'Reversal correction'

    target = models.ForeignKey(
        StocktakeTarget, on_delete=models.PROTECT, related_name='reconciliations',
    )
    phase = models.CharField(max_length=8, choices=Phase.choices)
    domain = models.CharField(max_length=32)
    result_app = models.CharField(max_length=64)
    result_model = models.CharField(max_length=64)
    result_object_id = models.PositiveBigIntegerField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reverses = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversed_by',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']


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
        Location,
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
    unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movements',
    )
    movement_type = models.CharField(max_length=24, choices=MovementType.choices)
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        validators=[MinValueValidator(POSITIVE_DECIMAL)],
    )
    source = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='outgoing_stock_movements',
    )
    destination = models.ForeignKey(
        Location,
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
        indexes = [
            models.Index(fields=['workspace', 'occurred_at'], name='stock_move_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='inventory_movement_positive_quantity',
            ),
            models.CheckConstraint(
                condition=(models.Q(source__isnull=True) | models.Q(destination__isnull=True) | ~models.Q(source=models.F('destination'))),
                name='inventory_movement_distinct_locations',
            ),
            models.CheckConstraint(
                condition=(models.Q(unit__isnull=True) | models.Q(quantity=1)),
                name='inventory_movement_serialized_quantity_one',
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
            'unit': self.unit if self.unit_id else None,
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
        if self.unit_id:
            if self.unit.source_lot_id != self.lot_id:
                errors['unit'] = 'The unit does not belong to this stock lot.'
        elif self.lot_id and self.lot.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
            # Deliberately serialized-only, not `INDIVIDUALLY_IDENTIFIED`. A
            # mixed lot holds both shapes at once: bulk quantity moves with no
            # unit, and a numbered pot moves with one.
            errors['unit'] = 'Serialized movements require an inventory unit.'
        return errors

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Stock movements are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stock movements cannot be deleted.')
