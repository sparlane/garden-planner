"""Workspace-scoped customers, commercial terms, and stock reservations."""

# pylint: disable=duplicate-code,too-many-lines

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from inventory.models import InventoryItem, InventoryUnit, MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from plantings.models import SpecificPlant
from plants.models import PlantVariety
from workspaces.models import Workspace, WorkspaceOwnedModel

from .calculations import calculate_line, refresh_order_totals


ZERO_MONEY = Decimal('0.0000')
EDITABLE_ORDER_STATUSES = ('quote', 'draft')


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
        TRAY = 'tray', 'Serialized tray'

    class DiscountType(models.TextChoices):
        """How the entered discount value is interpreted."""

        NONE = 'none', 'No discount'
        FIXED = 'fixed', 'Fixed amount'
        PERCENTAGE = 'percentage', 'Percentage'

    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    line_type = models.CharField(max_length=16, choices=LineType.choices)
    variety = models.ForeignKey(PlantVariety, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    tray_item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, validators=[MinValueValidator(ZERO_MONEY)])
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
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
                condition=(models.Q(line_type='seedling', variety__isnull=False, tray_item__isnull=True) | models.Q(line_type='tray', variety__isnull=True, tray_item__isnull=False)),
                name='sales_line_target_matches_type',
            ),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name='sales_line_quantity_positive'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.order.status not in EDITABLE_ORDER_STATUSES:
            errors['order'] = 'Confirmed commercial terms are immutable.'
        if self.line_type == self.LineType.SEEDLING:
            if not self.variety_id or self.tray_item_id:
                errors['variety'] = 'A seedling line requires one variety.'
            elif self.variety.workspace_id != self.order.workspace_id:
                errors['variety'] = 'The variety belongs to a different workspace.'
        elif self.line_type == self.LineType.TRAY:
            if not self.tray_item_id or self.variety_id:
                errors['tray_item'] = 'A tray line requires one inventory item.'
            elif self.tray_item.workspace_id != self.order.workspace_id:
                errors['tray_item'] = 'The tray item belongs to a different workspace.'
            elif self.tray_item.category != InventoryItem.Category.TRAY or self.tray_item.tracking_mode != InventoryItem.TrackingMode.SERIALIZED:
                errors['tray_item'] = 'Select a serialized tray inventory item.'
        entered_gross = Decimal(self.quantity or 0) * Decimal(self.unit_price or 0)
        if self.discount_type == self.DiscountType.NONE and self.discount_value != ZERO_MONEY:
            errors['discount_value'] = 'No-discount lines require a zero value.'
        if self.discount_type == self.DiscountType.PERCENTAGE and self.discount_value > 100:
            errors['discount_value'] = 'A percentage discount cannot exceed 100.'
        if self.discount_type == self.DiscountType.FIXED and self.discount_value > entered_gross:
            errors['discount_value'] = 'A fixed discount cannot exceed the line gross.'
        if errors:
            raise ValidationError(errors)

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
    """One concrete plant or serialized tray selected for a line."""

    class Status(models.TextChoices):
        """Reservation state, including task 45's future fulfillment state."""

        PENDING = 'pending', 'Pending'
        RESERVED = 'reserved', 'Reserved'
        RELEASED = 'released', 'Released'
        EXPIRED = 'expired', 'Expired'
        FULFILLED = 'fulfilled', 'Fulfilled'

    line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name='allocations')
    plant = models.ForeignKey(SpecificPlant, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_allocations')
    inventory_unit = models.ForeignKey(InventoryUnit, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_allocations')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(plant__isnull=False, inventory_unit__isnull=True) | models.Q(plant__isnull=True, inventory_unit__isnull=False)),
                name='sales_allocation_exactly_one_target',
            ),
            models.UniqueConstraint(fields=['plant'], condition=models.Q(status='reserved'), name='sales_one_active_plant_reservation'),
            models.UniqueConstraint(fields=['inventory_unit'], condition=models.Q(status='reserved'), name='sales_one_active_unit_reservation'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (self.plant_id is None) == (self.inventory_unit_id is None):
            errors['plant'] = 'Select exactly one plant or serialized unit.'
        elif self.plant_id:
            if self.line.line_type != SalesOrderLine.LineType.SEEDLING:
                errors['plant'] = 'Plants can only be allocated to seedling lines.'
            elif self.plant.workspace_id != self.line.order.workspace_id:
                errors['plant'] = 'The plant belongs to a different workspace.'
            elif self.plant.batch.variety_id != self.line.variety_id:
                errors['plant'] = 'The plant is a different variety from this line.'
        else:
            if self.line.line_type != SalesOrderLine.LineType.TRAY:
                errors['inventory_unit'] = 'Units can only be allocated to tray lines.'
            elif self.inventory_unit.workspace_id != self.line.order.workspace_id:
                errors['inventory_unit'] = 'The unit belongs to a different workspace.'
            elif self.inventory_unit.item_id != self.line.tray_item_id:
                errors['inventory_unit'] = 'The unit is a different item from this line.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.line_id != self.line_id or previous.plant_id != self.plant_id or previous.inventory_unit_id != self.inventory_unit_id:
                raise ValidationError('Allocation identities are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.PENDING:
            raise ValidationError('Reservation history cannot be deleted.')
        return super().delete(*args, **kwargs)


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
