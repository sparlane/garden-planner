"""Tax-oriented records that deliberately stop short of a general ledger."""

# Financial records intentionally repeat actor/audit fields and contain nested
# choice types whose names document the stored wire values.
# pylint: disable=duplicate-code,missing-class-docstring

from datetime import date
from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from workspaces.conversion import (
    RATE_DIGITS,
    RATE_PLACES,
    ConversionMethod,
    QuoteDirection,
    conversion_policy_refusal,
)
from workspaces.models import WorkspaceOwnedModel


ZERO = Decimal('0')
ONE = Decimal('1')
MONEY_DIGITS = 18
MONEY_PLACES = 4


class ValidatedModel(models.Model):
    """Validate domain invariants on every ordinary save."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AppendOnlyModel(ValidatedModel):
    """A record corrected by a compensating record, never by mutation."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Posted bookkeeping records are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Posted bookkeeping records cannot be deleted.')


class Liability(WorkspaceOwnedModel, ValidatedModel):
    """A named non-ledger obligation used to reconcile borrowed money."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    counterparty = models.CharField(max_length=255)
    opened_on = models.DateField()
    closed_on = models.DateField(null=True, blank=True)
    currency_code = models.CharField(max_length=3)
    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'code'], name='bookkeeping_liability_code_unique',
        )]

    def clean(self):
        super().clean()
        if self.closed_on and self.closed_on < self.opened_on:
            raise ValidationError({'closed_on': 'The close date cannot precede the open date.'})


class BookkeepingEntry(WorkspaceOwnedModel, AppendOnlyModel):
    """One immutable non-sales/non-purchasing money fact."""

    class Kind(models.TextChoices):
        OTHER_INCOME = 'other_income', 'Other income'
        OWNER_CONTRIBUTION = 'owner_contribution', 'Owner contribution'
        OWNER_DRAWING = 'owner_drawing', 'Owner drawing'
        LIABILITY_ADVANCE = 'liability_advance', 'Liability advance'
        LIABILITY_REPAYMENT = 'liability_repayment', 'Liability repayment'
        CASH_ADJUSTMENT = 'cash_adjustment', 'Cash adjustment'

    kind = models.CharField(max_length=32, choices=Kind.choices)
    occurred_on = models.DateField()
    description = models.CharField(max_length=255)
    counterparty = models.CharField(max_length=255, blank=True, default='')
    liability = models.ForeignKey(
        Liability, on_delete=models.PROTECT, null=True, blank=True,
        related_name='entries',
    )
    amount_ex_tax = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES,
        validators=[MinValueValidator(ZERO)],
    )
    tax_amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    tax_treatment = models.CharField(max_length=32, default='out_of_scope')
    currency_code = models.CharField(max_length=3)
    account_reference = models.CharField(max_length=255, blank=True, default='')
    external_reference = models.CharField(max_length=255, blank=True, default='')
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    reversal_of = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='reversal',
    )
    operation_key = models.UUIDField(default=uuid.uuid4)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['occurred_on', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'operation_key'],
            name='bookkeeping_entry_operation_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.total_incl_tax != self.amount_ex_tax + self.tax_amount:
            errors['total_incl_tax'] = 'The total must equal the amount plus tax.'
        liability_kinds = {self.Kind.LIABILITY_ADVANCE, self.Kind.LIABILITY_REPAYMENT}
        if (self.kind in liability_kinds) != bool(self.liability_id):
            errors['liability'] = 'Liability entries require a liability; other entries must omit it.'
        if self.liability_id and self.liability.workspace_id != self.workspace_id:
            errors['liability'] = 'The liability belongs to another workspace.'
        if self.kind != self.Kind.OTHER_INCOME and self.tax_amount != ZERO:
            errors['tax_amount'] = 'Only other income can carry tax.'
        if self.reversal_of_id and self.reversal_of.workspace_id != self.workspace_id:
            errors['reversal_of'] = 'The original entry belongs to another workspace.'
        if errors:
            raise ValidationError(errors)


class TaxAsset(WorkspaceOwnedModel, ValidatedModel):
    """An accounting asset register independent of operational equipment."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=128)
    acquired_on = models.DateField()
    first_used_on = models.DateField(null=True, blank=True)
    cost_incl_tax = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    recoverable_tax = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=ZERO)
    tax_cost = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    currency_code = models.CharField(max_length=3)
    supplier_expense = models.ForeignKey(
        'purchasing.BusinessExpense', on_delete=models.PROTECT, null=True,
        blank=True, related_name='tax_assets',
    )
    disposed_on = models.DateField(null=True, blank=True)
    disposal_proceeds = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES,
        null=True, blank=True,
    )
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'code'], name='bookkeeping_tax_asset_code_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.tax_cost != self.cost_incl_tax - self.recoverable_tax:
            errors['tax_cost'] = 'Tax cost must equal cost less recoverable tax.'
        if self.disposed_on and self.disposed_on < self.acquired_on:
            errors['disposed_on'] = 'Disposal cannot precede acquisition.'
        if bool(self.disposed_on) != (self.disposal_proceeds is not None):
            errors['disposal_proceeds'] = 'Provide both disposal date and proceeds.'
        if self.supplier_expense_id and self.supplier_expense.workspace_id != self.workspace_id:
            errors['supplier_expense'] = 'The expense belongs to another workspace.'
        if errors:
            raise ValidationError(errors)


class DepreciationSchedule(WorkspaceOwnedModel, AppendOnlyModel):
    """An operator-approved annual depreciation calculation."""

    class Method(models.TextChoices):
        DIMINISHING_VALUE = 'dv', 'Diminishing value'
        STRAIGHT_LINE = 'sl', 'Straight line'
        NONE = 'none', 'No depreciation claimed'

    asset = models.ForeignKey(TaxAsset, on_delete=models.PROTECT, related_name='schedules')
    income_year_end = models.DateField()
    method = models.CharField(max_length=8, choices=Method.choices)
    rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4, validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
    )
    business_use_percent = models.DecimalField(
        max_digits=7, decimal_places=4, validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal('100'))],
    )
    months_used = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    opening_tax_value = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    depreciation_claimed = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    disposal_adjustment = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=ZERO)
    closing_tax_value = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['income_year_end', 'asset_id']
        constraints = [models.UniqueConstraint(
            fields=['asset', 'income_year_end'], name='bookkeeping_asset_year_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.asset_id and self.asset.workspace_id != self.workspace_id:
            errors['asset'] = 'The asset belongs to another workspace.'
        expected = self.opening_tax_value - self.depreciation_claimed + self.disposal_adjustment
        if self.closing_tax_value != max(expected, ZERO):
            errors['closing_tax_value'] = 'Closing value does not reconcile to the entered schedule.'
        if self.method == self.Method.NONE and self.depreciation_claimed != ZERO:
            errors['depreciation_claimed'] = 'No-depreciation schedules must claim zero.'
        if errors:
            raise ValidationError(errors)


class IncomeTaxYear(WorkspaceOwnedModel, ValidatedModel):
    """One versioned normal-balance-date income-tax working-paper set."""

    class Basis(models.TextChoices):
        CASH = 'cash', 'Cash'
        ACCRUAL = 'accrual', 'Accrual'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        FINALIZED = 'finalized', 'Finalized'

    year_end = models.DateField()
    basis = models.CharField(max_length=8, choices=Basis.choices)
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='superseded_by',
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False)
    notes = models.TextField(blank=True, default='')
    frozen_report = models.JSONField(default=dict, blank=True, editable=False)
    finalized_at = models.DateTimeField(null=True, blank=True, editable=False)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name='+')
    retain_until = models.DateField(editable=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year_end', '-revision']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'year_end', 'revision'], name='bookkeeping_income_year_revision_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if (self.year_end.month, self.year_end.day) != (3, 31):
            errors['year_end'] = 'Normal New Zealand income years must end on 31 March.'
        if self.supersedes_id:
            if self.supersedes.workspace_id != self.workspace_id or self.supersedes.year_end != self.year_end:
                errors['supersedes'] = 'A revision must supersede the same workspace and income year.'
            elif self.revision != self.supersedes.revision + 1:
                errors['revision'] = 'A revision must immediately follow the superseded revision.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.retain_until = date(self.year_end.year + 7, 3, 31)
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only('status').first()
            if previous and previous.status == self.Status.FINALIZED:
                raise ValidationError('Finalized income-tax years are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.FINALIZED:
            raise ValidationError('Finalized income-tax years cannot be deleted.')
        return super().delete(*args, **kwargs)


class StockValuationLine(ValidatedModel):
    """A frozen derived or evidenced manual year-end stock value."""

    class Category(models.TextChoices):
        SALEABLE_PLANTS = 'saleable_plants', 'Saleable plants'
        WORK_IN_PROGRESS = 'work_in_progress', 'Work in progress'
        SEED_MEDIA = 'seed_media', 'Seed and media'
        PACKAGING = 'packaging', 'Packaging'
        OTHER = 'other', 'Other trading stock'

    class Method(models.TextChoices):
        COST = 'cost', 'Cost'
        DISCOUNTED_SELLING = 'discounted_selling', 'Discounted selling price'
        REPLACEMENT = 'replacement', 'Replacement price'
        MARKET_SELLING = 'market_selling', 'Market selling value'
        OPENING_VALUE = 'opening_value', 'Opening-value concession'

    income_year = models.ForeignKey(IncomeTaxYear, on_delete=models.CASCADE, related_name='stock_lines')
    category = models.CharField(max_length=32, choices=Category.choices)
    description = models.CharField(max_length=255)
    source_type = models.CharField(max_length=64, blank=True, default='manual')
    source_id = models.CharField(max_length=128, blank=True, default='')
    quantity = models.DecimalField(max_digits=24, decimal_places=9, null=True, blank=True)
    unit_code = models.CharField(max_length=16, blank=True, default='')
    original_cost = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True)
    method = models.CharField(max_length=32, choices=Method.choices)
    value = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    currency_code = models.CharField(max_length=3)
    evidence_url = models.URLField(max_length=2048, blank=True, default='')
    assumptions = models.TextField(blank=True, default='')
    derived = models.BooleanField(default=False, editable=False)
    provisional = models.BooleanField(default=False, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'source_type', 'source_id', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['income_year', 'source_type', 'source_id'],
            name='bookkeeping_stock_source_unique',
        )]

    def clean(self):
        super().clean()
        errors = {}
        if self.income_year_id and self.income_year.status != IncomeTaxYear.Status.DRAFT:
            errors['income_year'] = 'Finalized valuations cannot be changed.'
        if self.value < ZERO:
            errors['value'] = 'Stock value cannot be negative.'
        if self.method == self.Method.MARKET_SELLING and self.original_cost is not None and self.value > self.original_cost:
            errors['value'] = 'Market selling value can only be used below cost.'
        if not self.derived and not self.evidence_url:
            errors['evidence_url'] = 'Manual valuation lines require evidence.'
        if errors:
            raise ValidationError(errors)


class TaxRetentionRecord(WorkspaceOwnedModel, AppendOnlyModel):
    """A stable source identity protected through its statutory retention date."""

    source_type = models.CharField(max_length=64)
    source_id = models.CharField(max_length=128)
    income_year_end = models.DateField()
    retain_until = models.DateField()
    legal_hold = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['source_type', 'source_id']
        constraints = [models.UniqueConstraint(
            fields=['workspace', 'source_type', 'source_id'],
            name='bookkeeping_retained_source_unique',
        )]


class LegalHoldEvent(WorkspaceOwnedModel, AppendOnlyModel):
    """An append-only activation or release of a retained record's legal hold."""

    retention = models.ForeignKey(TaxRetentionRecord, on_delete=models.PROTECT, related_name='hold_events')
    active = models.BooleanField()
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.retention_id and self.retention.workspace_id != self.workspace_id:
            raise ValidationError({'retention': 'The retained source belongs to another workspace.'})


class CurrencyConversion(WorkspaceOwnedModel, AppendOnlyModel):
    """What one transaction's amounts come to in the workspace's currency.

    The rate is typed against the transaction it converts, and there is no rate
    table anywhere to read one back out of. This record is therefore the
    exchange-rate record itself: the source it was taken from, the rate, which
    way round the rate is quoted, the date it was effective, the date the
    conversion was made, and the method -- beside the amounts it was applied
    to, so what was converted is readable years later without recomputing it.

    It is a separate record because the transaction cannot hold it. A rate
    often arrives after the transaction does, and `billing.SupplyDocument`,
    `sales.Payment` and `sales.Refund` are immutable commerce records while
    `BookkeepingEntry` above is append-only, so there is no column on the row
    to write it into. It points at the row by `source_type` and `source_id`,
    the way `TaxRetentionRecord` does, which is also what lets one record serve
    nine kinds of row across four apps.

    A wrong rate is corrected by recording the right one against the same
    source, with `supersedes` naming the one it replaces. Nothing is mutated
    and nothing is deleted; the superseded conversion stays readable, which is
    most of why a conversion is a record rather than a column. The live
    conversion for a source is the one nothing supersedes.

    A row already in the workspace's own currency gets one of these too, at a
    rate of one and the `base_currency` method. It is not a conversion in any
    real sense, but recording it means every taxable transaction carries a
    conversion, so a row without one is unambiguously a row whose rate has not
    been typed yet rather than a row that never needed one.
    """

    source_type = models.CharField(max_length=64)
    source_id = models.CharField(max_length=128)
    source_currency_code = models.CharField(max_length=3)
    target_currency_code = models.CharField(
        max_length=3,
        help_text='The workspace currency as it stood when the rate was typed.',
    )
    rate = models.DecimalField(max_digits=RATE_DIGITS, decimal_places=RATE_PLACES)
    quote_direction = models.CharField(max_length=24, choices=QuoteDirection.choices)
    method = models.CharField(max_length=16, choices=ConversionMethod.choices)
    rate_source = models.CharField(
        max_length=255,
        help_text='Where the rate was taken from, in the words of whoever took it.',
    )
    effective_date = models.DateField(help_text='The date the rate applied on.')
    converted_on = models.DateField(help_text='The date the conversion was made.')
    amounts = models.JSONField(
        default=list,
        help_text=(
            'One entry per converted amount: its name on the source row, what '
            'the row carries, and what that comes to at this rate.'
        ),
    )
    supersedes = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='superseded_by',
    )
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False, related_name='+')
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['source_type', 'source_id', 'pk']
        indexes = [
            models.Index(
                fields=['workspace', 'source_type', 'source_id'],
                name='bookkeeping_conversion_idx',
            ),
        ]

    def clean(self):
        """Refuse a rate that could not have converted this row."""
        super().clean()
        errors = {}
        if self.rate is not None and self.rate <= ZERO:
            errors['rate'] = 'A conversion rate must be above zero.'
        errors.update(self._currency_errors())
        if self.workspace_id and self.method:
            refusal = conversion_policy_refusal(self.workspace, self.method)
            if refusal:
                errors['method'] = refusal
        if self.supersedes_id:
            errors.update(self._supersedes_errors())
        if errors:
            raise ValidationError(errors)

    def _currency_errors(self):
        """Check the pair against the method, which is what makes it identity."""
        same = self.source_currency_code == self.target_currency_code
        if self.method == ConversionMethod.BASE_CURRENCY:
            if not same:
                return {'method': 'A base-currency conversion cannot change the currency.'}
            if self.rate is not None and self.rate != ONE:
                return {'rate': 'A base-currency conversion is recorded at a rate of one.'}
            return {}
        if same:
            return {'source_currency_code': (
                'An amount already in the workspace currency is recorded as a '
                'base-currency conversion rather than converted.'
            )}
        return {}

    def _supersedes_errors(self):
        """A correction replaces one conversion of the same row, and only one."""
        earlier = CurrencyConversion.objects.filter(pk=self.supersedes_id).first()
        if earlier is None:
            return {}
        if earlier.workspace_id != self.workspace_id:
            return {'supersedes': 'The earlier conversion belongs to another workspace.'}
        if (earlier.source_type, earlier.source_id) != (self.source_type, self.source_id):
            return {'supersedes': 'The earlier conversion converted a different record.'}
        return {}
