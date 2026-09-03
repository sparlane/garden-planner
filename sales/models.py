"""Workspace-scoped customers, commercial terms, and stock reservations."""

# pylint: disable=duplicate-code,too-many-lines

import operator
from decimal import Decimal
from functools import reduce

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from inventory.models import InventoryItem, InventoryUnit, MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from inventory.models import StockLot, StockMovement
from inventory.units import UnitCode
from locations.models import Location
from plantings.models import PlantLifecycleEvent, SpecificPlant
from plants.models import PlantVariety
from workspaces.models import Workspace, WorkspaceOwnedModel

from .calculations import calculate_line, refresh_order_totals


ZERO_MONEY = Decimal('0.0000')
EDITABLE_ORDER_STATUSES = ('quote', 'draft')

#: Tracking modes that keep an anonymous pool a counted line can draw on. A
#: serialized item has none: every one of its units is somebody's identity.
LOT_BACKED_TRACKING_MODES = frozenset({
    InventoryItem.TrackingMode.LOT,
    InventoryItem.TrackingMode.MIXED,
})

#: The columns that can hold what a `SalesOrderAllocation` promises. Exactly
#: one is filled, which is what the generated identity constraint says.
ALLOCATION_TARGET_FIELDS = ('plant', 'inventory_unit', 'stock_lot')

#: The columns only a counted draw on a lot uses. An identity is one thing
#: standing somewhere known, so naming a place and a count for it would be two
#: ways to say the same figure, and they would be free to disagree.
COUNTED_ALLOCATION_FIELDS = ('source_location', 'quantity')


def _allocation_identity_condition():
    """Generate 'exactly one target, counted only when it is a lot'.

    Written out by hand this is one four-hundred-character line, and adding a
    fourth target later would mean editing every disjunct. Generating it from
    the column names keeps the database's rule and the model's fields the same
    statement, the way `costing.models.CostAllocation` does.
    """
    shapes = []
    for chosen in ALLOCATION_TARGET_FIELDS:
        nulls = {
            f'{field}__isnull': field != chosen
            for field in ALLOCATION_TARGET_FIELDS
        }
        nulls.update({
            f'{field}__isnull': chosen != 'stock_lot'
            for field in COUNTED_ALLOCATION_FIELDS
        })
        shapes.append(models.Q(**nulls))
    return reduce(operator.or_, shapes)


class Customer(WorkspaceOwnedModel):
    """A reusable customer identity; walk-in orders may omit one."""

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=64, blank=True, default='')
    billing_address = models.TextField(blank=True, default='')
    delivery_address = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.name.strip():
            raise ValidationError({'name': 'A customer name is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Customers must be deactivated, not deleted.')


class SalesOrderNumberSequence(models.Model):
    """The next readable order number for one locked workspace."""

    workspace = models.OneToOneField(
        Workspace,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name='+',
    )
    next_number = models.PositiveBigIntegerField(default=1)


class SalesOrder(WorkspaceOwnedModel):
    """One quote or accepted commercial promise."""

    class Status(models.TextChoices):
        """Commercial and future fulfillment states."""

        QUOTE = 'quote', 'Quote'
        DRAFT = 'draft', 'Draft'
        CONFIRMED = 'confirmed', 'Confirmed'
        PARTIALLY_FULFILLED = 'partially_fulfilled', 'Partially fulfilled'
        FULFILLED = 'fulfilled', 'Fulfilled'
        CANCELLED = 'cancelled', 'Cancelled'

    order_number = models.CharField(max_length=32)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sales_orders',
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, editable=False)
    quote_date = models.DateField(null=True, blank=True)
    order_date = models.DateField(null=True, blank=True)
    requested_date = models.DateField(null=True, blank=True)
    currency_code = models.CharField(
        max_length=3,
        validators=[RegexValidator(regex=r'^[A-Z]{3}$', message='Enter a three-letter uppercase ISO 4217 currency code.')],
    )
    prices_include_tax = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default='')
    gross_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    discount_total_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created', '-pk']
        indexes = [
            models.Index(fields=['workspace', 'status', 'requested_date'], name='sales_order_report_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'order_number'], name='sales_order_workspace_number_unique'),
        ]

    def __str__(self):
        return self.order_number

    def clean(self):
        super().clean()
        errors = {}
        if self.customer_id and self.customer.workspace_id != self.workspace_id:
            errors['customer'] = 'The customer belongs to a different workspace.'
        if self.status == self.Status.QUOTE and self.quote_date is None:
            errors['quote_date'] = 'A quote date is required for a quote.'
        if self.status != self.Status.QUOTE and self.status != self.Status.CANCELLED and self.order_date is None:
            errors['order_date'] = 'An order date is required.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status not in EDITABLE_ORDER_STATUSES:
                raise ValidationError('Confirmed or cancelled orders are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status not in EDITABLE_ORDER_STATUSES:
            raise ValidationError('Only quotes and drafts can be deleted.')
        return super().delete(*args, **kwargs)


class SalesOrderLine(models.Model):
    """Snapshotted commercial terms for one kind of promised stock."""

    class LineType(models.TextChoices):
        """The exact physical identities this line may allocate."""

        SEEDLING = 'seedling', 'Seedling'
        # Named for the mechanism rather than for trays: any individually
        # identified stock is sold this way, and a numbered pot is sold as
        # itself rather than dissolved back into anonymous stock first.
        UNIT = 'unit', 'Individually numbered unit'
        # Named for the mechanism rather than for pots: anything counted in
        # `each` and sold by the count rather than by identity goes out this
        # way, so a crate follows a pot without a third line type.
        LOT_QUANTITY = 'lot_quantity', 'Counted stock from a lot'

    class DiscountType(models.TextChoices):
        """How the entered discount value is interpreted."""

        NONE = 'none', 'No discount'
        FIXED = 'fixed', 'Fixed amount'
        PERCENTAGE = 'percentage', 'Percentage'

    class TaxTreatment(models.TextChoices):
        """What kind of supply this line is for GST, which a rate cannot say.

        A rate of zero is three different things — a zero-rated export, an
        exempt supply, and something outside GST altogether — and a GST return
        reports the first separately from the other two. `UNCLASSIFIED` is the
        honest state for a zero-rated-looking line nobody has yet said which
        of the three it is; it is never counted as zero-rated by default.
        """

        STANDARD = 'standard', 'Standard-rated'
        ZERO_RATED = 'zero_rated', 'Zero-rated'
        EXEMPT = 'exempt', 'Exempt'
        OUT_OF_SCOPE = 'out_of_scope', 'Outside the scope of GST'
        UNCLASSIFIED = 'unclassified', 'Not yet classified'

    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    line_type = models.CharField(max_length=16, choices=LineType.choices)
    variety = models.ForeignKey(PlantVariety, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO_MONEY)])
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
    # Left blank on the way in and derived in clean(): a rate above zero is a
    # standard-rated supply by definition, while a rate of zero is genuinely
    # unknown until somebody classifies it. Stored blank is refused by a check
    # constraint, so a writer that skips validation cannot leave one behind.
    tax_treatment = models.CharField(max_length=16, choices=TaxTreatment.choices, blank=True, default='')
    discount_type = models.CharField(max_length=16, choices=DiscountType.choices, default=DiscountType.NONE)
    discount_value = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, validators=[MinValueValidator(ZERO_MONEY)])
    gross_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    discount_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    subtotal_ex_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    tax_total = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    total_incl_tax = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=ZERO_MONEY, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(line_type='seedling', variety__isnull=False, item__isnull=True) | models.Q(line_type__in=('unit', 'lot_quantity'), variety__isnull=True, item__isnull=False)),
                name='sales_line_target_matches_type',
            ),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name='sales_line_quantity_positive'),
            # A standard-rated supply at a zero rate, or a zero-rated one at 15%,
            # would each put the wrong figure in a different box of the return.
            models.CheckConstraint(
                condition=(models.Q(tax_treatment='standard', tax_rate__gt=0) | models.Q(tax_treatment__in=('zero_rated', 'exempt', 'out_of_scope', 'unclassified'), tax_rate=0)),
                name='sales_line_tax_treatment_matches_rate',
            ),
        ]

    def _target_errors(self):
        """Validate the catalog target this kind of line has to name."""
        if self.line_type == self.LineType.SEEDLING:
            return self._variety_target_errors()
        if self.line_type == self.LineType.UNIT:
            return self._item_target_errors()
        if self.line_type == self.LineType.LOT_QUANTITY:
            return self._lot_item_target_errors()
        return {}

    def _variety_target_errors(self):
        """A seedling line promises a variety and nothing more exact."""
        if not self.variety_id or self.item_id:
            return {'variety': 'A seedling line requires one variety.'}
        if self.variety.workspace_id != self.order.workspace_id:
            return {'variety': 'The variety belongs to a different workspace.'}
        return {}

    def _item_target_errors(self):
        """A unit line promises identified stock: a tray, or a numbered pot."""
        if not self.item_id or self.variety_id:
            return {'item': 'A unit line requires one inventory item.'}
        if self.item.workspace_id != self.order.workspace_id:
            return {'item': 'The item belongs to a different workspace.'}
        if self.item.tracking_mode not in InventoryItem.INDIVIDUALLY_IDENTIFIED:
            return {'item': 'Select an individually identified inventory item.'}
        if self.item.base_unit != UnitCode.EACH:
            return {'item': 'Individually sold stock is counted in each.'}
        return {}

    def _lot_item_target_errors(self):
        """A counted line promises an item by the count, not by identity.

        Anonymous stock is only countable if the item is lot-controlled, and
        only sellable a whole one at a time if it is counted in `each`. A
        purely serialized item has no anonymous pool to draw from at all.
        """
        if not self.item_id or self.variety_id:
            return {'item': 'A counted line requires one inventory item.'}
        if self.item.workspace_id != self.order.workspace_id:
            return {'item': 'The item belongs to a different workspace.'}
        if self.item.tracking_mode not in LOT_BACKED_TRACKING_MODES:
            return {'item': 'Select a lot-tracked or mixed inventory item.'}
        if self.item.base_unit != UnitCode.EACH:
            return {'item': 'Counted stock is sold in each.'}
        return {}

    def clean(self):
        super().clean()
        errors = {}
        self._derive_tax_treatment()
        if self.order.status not in EDITABLE_ORDER_STATUSES:
            errors['order'] = 'Confirmed commercial terms are immutable.'
        errors.update(self._target_errors())
        entered_gross = Decimal(self.quantity or 0) * Decimal(self.unit_price or 0)
        if self.discount_type == self.DiscountType.NONE and self.discount_value != ZERO_MONEY:
            errors['discount_value'] = 'No-discount lines require a zero value.'
        if self.discount_type == self.DiscountType.PERCENTAGE and self.discount_value > 100:
            errors['discount_value'] = 'A percentage discount cannot exceed 100.'
        if self.discount_type == self.DiscountType.FIXED and self.discount_value > entered_gross:
            errors['discount_value'] = 'A fixed discount cannot exceed the line gross.'
        errors.update(self._tax_treatment_errors())
        if errors:
            raise ValidationError(errors)

    def _derive_tax_treatment(self):
        """Fill an unstated treatment from what the rate already establishes.

        A rate above zero is a standard-rated supply; there is nothing to ask.
        A rate of zero is a zero-rated export, an exempt supply, or something
        outside GST, and guessing between them would put a figure in the wrong
        box of a return — so it stays unclassified until somebody says.
        """
        if self.tax_treatment:
            return
        rate = Decimal(self.tax_rate or 0)
        self.tax_treatment = (
            self.TaxTreatment.STANDARD if rate > 0 else self.TaxTreatment.UNCLASSIFIED
        )

    def _tax_treatment_errors(self):
        """Refuse a treatment the rate contradicts."""
        rate = Decimal(self.tax_rate or 0)
        if self.tax_treatment == self.TaxTreatment.STANDARD and rate <= 0:
            return {'tax_treatment': 'A standard-rated line needs a tax rate above zero.'}
        if self.tax_treatment != self.TaxTreatment.STANDARD and rate > 0:
            return {'tax_treatment': 'Only a standard-rated line carries a tax rate.'}
        return {}

    def save(self, *args, **kwargs):
        self.full_clean()
        amounts = calculate_line(self)
        for field, value in zip(
            ('gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax'),
            amounts,
        ):
            setattr(self, field, value)
        super().save(*args, **kwargs)
        refresh_order_totals(self.order)

    def delete(self, *args, **kwargs):
        if self.order.status not in EDITABLE_ORDER_STATUSES:
            raise ValidationError('Confirmed commercial terms are immutable.')
        order = self.order
        result = super().delete(*args, **kwargs)
        refresh_order_totals(order)
        return result


class SalesOrderAllocation(models.Model):
    """One promise of stock: an identity, or a count drawn from one lot.

    A plant and a numbered unit are each exactly one thing, so for them the
    allocation *is* the quantity and `quantity` stays null. Anonymous stock
    has no identity to point at, so a lot allocation names the lot, the place
    it is standing, and how many — and its availability is arithmetic over
    `inventory.ledger.bulk_balance` rather than a unique index, because many
    orders may legitimately hold parts of one lot at once.
    """

    class Status(models.TextChoices):
        """Reservation state, including task 45's future fulfillment state."""

        PENDING = 'pending', 'Pending'
        RESERVED = 'reserved', 'Reserved'
        RELEASED = 'released', 'Released'
        EXPIRED = 'expired', 'Expired'
        FULFILLED = 'fulfilled', 'Fulfilled'
        RETURNED = 'returned', 'Returned'

    line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='allocations')
    plant = models.ForeignKey(SpecificPlant, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_allocations')
    inventory_unit = models.ForeignKey(InventoryUnit, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_allocations')
    stock_lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_allocations')
    source_location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    # Null for an identity target, whose quantity is always exactly one. Task
    # 114 widens this to a decimal rather than inventing a column.
    quantity = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                condition=_allocation_identity_condition(),
                name='sales_allocation_exactly_one_target',
            ),
            models.CheckConstraint(
                condition=(models.Q(quantity__isnull=True) | models.Q(quantity__gte=1)),
                name='sales_allocation_quantity_positive',
            ),
            models.UniqueConstraint(fields=['plant'], condition=models.Q(status='reserved'), name='sales_one_active_plant_reservation'),
            models.UniqueConstraint(fields=['inventory_unit'], condition=models.Q(status='reserved'), name='sales_one_active_unit_reservation'),
        ]

    @property
    def target_kind(self):
        """Return which of the three targets this allocation names."""
        if self.plant_id:
            return 'plant'
        if self.inventory_unit_id:
            return 'inventory_unit'
        if self.stock_lot_id:
            return 'stock_lot'
        return None

    @property
    def promised_units(self):
        """Return how many of its line's units this one allocation covers.

        An identity is exactly one, which is why `quantity` is null on it
        rather than stored as a one nothing may contradict. It lives here
        because every reader of an allocation needs the same answer: a row is
        not a unit once one allocation can promise fifty pots, and a screen or
        a projection counting rows would call a covered line barely started.
        """
        return 1 if self.quantity is None else self.quantity

    def clean(self):
        super().clean()
        named = [
            bool(self.plant_id),
            bool(self.inventory_unit_id),
            bool(self.stock_lot_id),
        ]
        if sum(named) != 1:
            raise ValidationError(
                {'plant': 'Select exactly one plant, serialized unit, or lot.'},
            )
        errors = {
            'plant': self._plant_errors,
            'inventory_unit': self._unit_errors,
            'stock_lot': self._lot_errors,
        }[self.target_kind]()
        if errors:
            raise ValidationError(errors)

    def _plant_errors(self):
        """Require a sellable plant of this seedling line's own variety."""
        if self.line.line_type != SalesOrderLine.LineType.SEEDLING:
            return {'plant': 'Plants can only be allocated to seedling lines.'}
        if self.plant.workspace_id != self.line.order.workspace_id:
            return {'plant': 'The plant belongs to a different workspace.'}
        if self.plant.batch.variety_id != self.line.variety_id:
            return {'plant': 'The plant is a different variety from this line.'}
        return {}

    def _unit_errors(self):
        """Require a numbered unit of this unit line's own item."""
        if self.line.line_type != SalesOrderLine.LineType.UNIT:
            return {'inventory_unit': 'Units can only be allocated to unit lines.'}
        if self.inventory_unit.workspace_id != self.line.order.workspace_id:
            return {'inventory_unit': 'The unit belongs to a different workspace.'}
        if self.inventory_unit.item_id != self.line.item_id:
            return {'inventory_unit': 'The unit is a different item from this line.'}
        return {}

    def _lot_errors(self):
        """Require a counted draw on one lot of this line's own item."""
        if self.line.line_type != SalesOrderLine.LineType.LOT_QUANTITY:
            return {'stock_lot': 'Lots can only be allocated to counted lines.'}
        if self.stock_lot.workspace_id != self.line.order.workspace_id:
            return {'stock_lot': 'The lot belongs to a different workspace.'}
        if self.stock_lot.item_id != self.line.item_id:
            return {'stock_lot': 'The lot is a different item from this line.'}
        return self._counted_draw_errors()

    def _counted_draw_errors(self):
        """Require the place a count is drawn from, and the count itself."""
        if self.source_location_id is None:
            return {'source_location': 'A counted allocation requires the place it draws from.'}
        if self.source_location.workspace_id != self.line.order.workspace_id:
            return {'source_location': 'The location belongs to a different workspace.'}
        if not self.quantity:
            return {'quantity': 'A counted allocation requires a quantity of at least one.'}
        return {}

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            identity = ('line_id', 'plant_id', 'inventory_unit_id', 'stock_lot_id', 'source_location_id', 'quantity')
            if any(getattr(previous, name) != getattr(self, name) for name in identity):
                raise ValidationError('Allocation identities are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.PENDING:
            raise ValidationError('Reservation history cannot be deleted.')
        return super().delete(*args, **kwargs)


def active_allocation_prefetch():
    """Prefetch active target promises with their readable order reference."""
    return models.Prefetch(
        'sales_allocations',
        queryset=SalesOrderAllocation.objects.filter(
            status__in=[
                SalesOrderAllocation.Status.PENDING,
                SalesOrderAllocation.Status.RESERVED,
            ],
        ).select_related('line__order').order_by('line__order__order_number', 'pk'),
        to_attr='active_sales_allocations',
    )


class ReservationEvent(models.Model):
    """An immutable fact in one allocation's reservation lifecycle."""

    class EventType(models.TextChoices):
        """Supported reservation transitions."""

        RESERVED = 'reserved', 'Reserved'
        RELEASED = 'released', 'Released'
        EXPIRED = 'expired', 'Expired'
        CANCELLED = 'cancelled', 'Cancelled'
        FULFILLED = 'fulfilled', 'Fulfilled'

    allocation = models.ForeignKey(SalesOrderAllocation, on_delete=models.PROTECT, related_name='events')
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    occurred_at = models.DateTimeField()
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_at', 'pk']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Reservation events are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Reservation events cannot be deleted.')


class FulfillmentNumberSequence(models.Model):
    """The next readable fulfillment number for one locked workspace."""

    workspace = models.OneToOneField(
        Workspace, on_delete=models.PROTECT, primary_key=True, related_name='+',
    )
    next_number = models.PositiveBigIntegerField(default=1)


class ImmutableCommerceModel(WorkspaceOwnedModel):
    """Shared append-only identity for retryable commerce actions."""

    operation_key = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Posted commerce records are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Posted commerce records cannot be deleted.')


class Fulfillment(ImmutableCommerceModel):
    """One posted dispatch or the explicit reversal of one dispatch."""

    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name='fulfillments',
    )
    fulfillment_number = models.CharField(max_length=32)
    fulfilled_at = models.DateTimeField()
    notes = models.TextField(blank=True, default='')
    reversal_of = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversal',
    )

    class Meta:
        ordering = ['fulfilled_at', 'pk']
        indexes = [
            models.Index(fields=['workspace', 'fulfilled_at'], name='sales_fulfill_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'fulfillment_number'],
                name='sales_fulfillment_workspace_number_unique',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'operation_key'],
                name='sales_fulfillment_workspace_operation_unique',
            ),
        ]


class FulfillmentLine(models.Model):
    """Recognized revenue and direct cost for one exact allocation."""

    fulfillment = models.ForeignKey(
        Fulfillment, on_delete=models.PROTECT, related_name='lines',
    )
    allocation = models.ForeignKey(
        SalesOrderAllocation, on_delete=models.PROTECT,
        related_name='fulfillment_lines',
    )
    commercial_position = models.PositiveIntegerField()
    # Snapshotted beside the money for the same reason the money is: this is
    # the record of record for a GST return, and a later reclassification of
    # the order line must not silently restate a period already reported.
    tax_treatment = models.CharField(max_length=16, blank=True, default='')
    gross_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    discount_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    cogs_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        null=True, blank=True,
    )
    cogs_provisional = models.BooleanField(default=False)
    currency_code = models.CharField(max_length=3)
    lifecycle_event = models.OneToOneField(
        PlantLifecycleEvent, on_delete=models.PROTECT, null=True, blank=True,
        related_name='fulfillment_line',
    )
    stock_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, null=True, blank=True,
        related_name='fulfillment_line',
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['fulfillment', 'allocation'],
                name='sales_fulfillment_line_allocation_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(commercial_position__gte=1),
                name='sales_fulfillment_line_position_positive',
            ),
        ]


class FulfillmentRider(models.Model):
    """One plant that left inside a sold container.

    `FulfillmentLine.lifecycle_event` is a one-to-one, which is right for a
    line that sells one plant directly. A pot holding three of them produces
    three lifecycle events and has one slot, so the passengers get a row each
    instead — which is also what a return needs, to bring them back one by one.
    """

    fulfillment_line = models.ForeignKey(
        FulfillmentLine, on_delete=models.PROTECT, related_name='riders',
    )
    plant = models.ForeignKey(
        SpecificPlant, on_delete=models.PROTECT, related_name='sale_riders',
    )
    lifecycle_event = models.OneToOneField(
        PlantLifecycleEvent, on_delete=models.PROTECT,
        related_name='fulfillment_rider',
    )
    return_event = models.OneToOneField(
        PlantLifecycleEvent, on_delete=models.PROTECT, null=True, blank=True,
        related_name='returned_fulfillment_rider',
    )
    cogs_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        null=True, blank=True,
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['fulfillment_line', 'plant'],
                name='sales_fulfillment_rider_unique',
            ),
        ]

    def __str__(self):
        return f'Plant {self.plant_id} in fulfillment line {self.fulfillment_line_id}'


class FulfillmentPackagingLine(models.Model):
    """An exact packaging-lot quantity consumed by one fulfillment."""

    fulfillment = models.ForeignKey(
        Fulfillment, on_delete=models.PROTECT, related_name='packaging_lines',
    )
    lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name='fulfillment_packaging',
    )
    source = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name='+',
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=9)
    base_unit = models.CharField(max_length=16)
    unit_cost = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        null=True, blank=True,
    )
    cogs_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        null=True, blank=True,
    )
    currency_code = models.CharField(max_length=3)
    stock_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT,
        related_name='fulfillment_packaging_line',
    )

    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(
            condition=models.Q(quantity__gt=0),
            name='sales_fulfillment_packaging_quantity_positive',
        )]


class Payment(ImmutableCommerceModel):
    """Operational cash received, or an append-only reversal of it."""

    class Method(models.TextChoices):
        """Supported operational tender descriptions."""

        CASH = 'cash', 'Cash'
        CARD = 'card', 'Card'
        BANK_TRANSFER = 'bank_transfer', 'Bank transfer'
        OTHER = 'other', 'Other'

    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name='payments',
    )
    paid_on = models.DateField()
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    currency_code = models.CharField(max_length=3)
    method = models.CharField(max_length=16, choices=Method.choices)
    external_reference = models.CharField(max_length=255, blank=True, default='')
    account_reference = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    reversal_of = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversal',
    )

    class Meta:
        ordering = ['paid_on', 'pk']
        indexes = [
            models.Index(fields=['workspace', 'paid_on'], name='sales_payment_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='sales_payment_amount_positive',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'operation_key'],
                name='sales_payment_workspace_operation_unique',
            ),
        ]


class SalesReturn(ImmutableCommerceModel):
    """A physical return, independent from whether money is refunded."""

    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name='returns',
    )
    returned_at = models.DateTimeField()
    reason = models.TextField()
    notes = models.TextField(blank=True, default='')
    health_observation = models.ForeignKey(
        'health.HealthObservation', on_delete=models.PROTECT, null=True,
        blank=True, related_name='sales_returns',
    )
    quarantine_case = models.ForeignKey(
        'health.QuarantineCase', on_delete=models.PROTECT, null=True,
        blank=True, related_name='sales_returns',
    )
    reversal_of = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversal',
    )

    class Meta:
        ordering = ['returned_at', 'pk']
        indexes = [
            models.Index(fields=['workspace', 'returned_at'], name='sales_return_date_idx'),
        ]
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'operation_key'],
            name='sales_return_workspace_operation_unique',
        )]


class SalesReturnLine(models.Model):
    """The explicit outcome and linked facts for one returned allocation."""

    class Outcome(models.TextChoices):
        """Required physical disposition for returned exact stock."""

        AVAILABLE = 'available', 'Available'
        QUARANTINED = 'quarantined', 'Quarantined'
        DISCARDED = 'discarded', 'Discarded'

    sales_return = models.ForeignKey(
        SalesReturn, on_delete=models.PROTECT, related_name='lines',
    )
    fulfillment_line = models.ForeignKey(
        FulfillmentLine, on_delete=models.PROTECT, related_name='return_lines',
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    destination = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True,
        related_name='+',
    )
    lifecycle_event = models.OneToOneField(
        PlantLifecycleEvent, on_delete=models.PROTECT, null=True, blank=True,
        related_name='sales_return_line',
    )
    return_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, null=True, blank=True,
        related_name='sales_return_line',
    )
    discard_movement = models.OneToOneField(
        StockMovement, on_delete=models.PROTECT, null=True, blank=True,
        related_name='sales_return_discard_line',
    )

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(
            fields=['sales_return', 'fulfillment_line'],
            name='sales_return_line_fulfillment_unique',
        )]


class Refund(ImmutableCommerceModel):
    """A monetary correction classified against original fulfillment lines."""

    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name='refunds',
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name='refunds',
    )
    sales_return = models.ForeignKey(
        SalesReturn, on_delete=models.PROTECT, null=True, blank=True,
        related_name='refunds',
    )
    refunded_at = models.DateTimeField()
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    currency_code = models.CharField(max_length=3)
    reason = models.TextField()
    account_reference = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    reversal_of = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversal',
    )

    class Meta:
        ordering = ['refunded_at', 'pk']
        indexes = [
            models.Index(fields=['workspace', 'refunded_at'], name='sales_refund_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='sales_refund_amount_positive',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'operation_key'],
                name='sales_refund_workspace_operation_unique',
            ),
        ]


class RefundLine(models.Model):
    """One proportional piece of a refund assigned to recognized revenue."""

    refund = models.ForeignKey(
        Refund, on_delete=models.PROTECT, related_name='lines',
    )
    fulfillment_line = models.ForeignKey(
        FulfillmentLine, on_delete=models.PROTECT, related_name='refund_lines',
    )
    # Carried from the fulfillment line it credits, so a credit lands in the
    # same box of the return as the supply it reverses.
    tax_treatment = models.CharField(max_length=16, blank=True, default='')
    gross_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    discount_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(
            fields=['refund', 'fulfillment_line'],
            name='sales_refund_line_fulfillment_unique',
        )]
