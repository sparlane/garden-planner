"""Which records carry a rate, and how one is recorded against them.

`workspaces.conversion` says how a rate converts an amount. This says what a
rate is recorded *against*: nine kinds of row across four apps, each named by a
`source_type` that matches the one the GST entries and the income-year
schedules already use, so a conversion found here is the conversion of the row
a report is looking at rather than of something with a similar name.

Two rules are worth stating out loud.

**Only a settled row is converted.** A draft receipt, a draft supplier invoice
and a draft expense can all still have their amounts changed, and a stored
equivalent of an amount that then moves is worse than no equivalent at all.
Everything else on the list is already immutable when it exists.

**One live conversion per row.** A correction records a new conversion naming
the one it supersedes, so the live conversion is the one nothing supersedes and
the earlier rate stays readable. Recording a second conversion without saying
what it replaces is refused, because two live rates for one transaction is two
answers to the question a return asks once.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from django.apps import apps as global_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from workspaces.conversion import ConversionMethod, QuoteDirection, convert_amount

from .models import CurrencyConversion


ZERO = Decimal('0')
ONE = Decimal('1')

#: What an identity conversion says it took its rate from. It took it from the
#: fact that there was nothing to convert, and the phrase is stored rather than
#: left blank so an export row reads as a sentence.
BASE_CURRENCY_SOURCE = 'Already in the workspace currency'


@dataclass(frozen=True)
class ConversionSource:  # pylint: disable=too-many-instance-attributes
    """One kind of row a rate can be recorded against."""

    source_type: str
    label: str
    app_label: str
    model_name: str
    date_field: str
    amounts: Callable
    currency_path: str = 'currency_code'
    settled: dict = field(default_factory=dict)
    unsettled_refusal: str = ''
    select_related: tuple = ()
    prefetch_related: tuple = ()

    def model(self, registry=global_apps):
        """Resolve the model, through whichever app registry is in force.

        A migration passes its own historical registry. Every field this
        descriptor touches is a plain column or a foreign key, so a historical
        model answers exactly as the real one does.
        """
        return registry.get_model(self.app_label, self.model_name)

    def rows(self, workspace, registry=global_apps):
        """Return this workspace's rows of this kind that may carry a rate."""
        queryset = self.model(registry).objects.filter(
            workspace=workspace, **self.settled,
        )
        if self.select_related:
            queryset = queryset.select_related(*self.select_related)
        return queryset

    def currency_of(self, row):
        """Return the currency the row was recorded in.

        An input-tax adjustment has no currency of its own: it adjusts a claim
        made on a receipt, and it is in whatever that receipt was in.
        """
        value = row
        for step in self.currency_path.split('.'):
            value = getattr(value, step)
        return value

    def date_of(self, row, workspace):
        """Return the row's own business date, in the workspace's timezone."""
        value = getattr(row, self.date_field)
        if isinstance(value, datetime):
            return value.astimezone(ZoneInfo(workspace.timezone)).date()
        return value


def _document_amounts(row):
    """The three figures every tax document states about itself."""
    return (
        ('subtotal_ex_tax', row.subtotal_ex_tax),
        ('tax_total', row.tax_total),
        ('total_incl_tax', row.total_incl_tax),
    )


def _expense_amounts(row):
    """A business expense, and the two figures derived from it when confirmed."""
    return _document_amounts(row) + (
        ('recoverable_tax', row.recoverable_tax),
        ('deductible_amount', row.deductible_amount),
    )


def _payment_amounts(row):
    """Cash moved in one direction, which is the whole of a payment."""
    return (('amount', row.amount),)


def _receipt_amounts(row):
    """A receipt states its money on its lines, so its equivalents are totals.

    Converting the totals does not stop a reader converting a line: the rate is
    the row's, and every reader applies it to whatever amount it holds. What is
    stored here is what the receipt as a whole came to, which is the figure a
    purchases box is built from.
    """
    lines = list(row.lines.all())
    return (
        ('lines_ex_tax', sum((line.line_cost_ex_tax for line in lines), ZERO)),
        ('recoverable_input_tax', sum((line.recoverable_input_tax for line in lines), ZERO)),
        ('non_recoverable_tax', sum((line.non_recoverable_tax for line in lines), ZERO)),
        ('acquisition_amount', sum(
            (line.acquisition_amount for line in lines
             if line.acquisition_amount is not None), ZERO,
        )),
    )


#: Every row a tax figure is derived from, in the order the change list names
#: them: taxable supply information, GST, purchases, refunds, and adjustments.
SOURCES = (
    ConversionSource(
        source_type='supply_document', label='Taxable supply information',
        app_label='billing', model_name='SupplyDocument',
        date_field='issued_on', amounts=_document_amounts,
    ),
    ConversionSource(
        source_type='supply_correction', label='Supply correction',
        app_label='billing', model_name='SupplyCorrection',
        date_field='corrected_on', amounts=_document_amounts,
    ),
    ConversionSource(
        source_type='payment', label='Customer payment',
        app_label='sales', model_name='Payment',
        date_field='paid_on', amounts=_payment_amounts,
    ),
    ConversionSource(
        source_type='refund', label='Customer refund',
        app_label='sales', model_name='Refund',
        date_field='refunded_at', amounts=_payment_amounts,
    ),
    ConversionSource(
        source_type='stock_receipt', label='Stock receipt',
        app_label='inventory', model_name='StockReceipt',
        date_field='received_date', amounts=_receipt_amounts,
        settled={'status': 'posted'}, prefetch_related=('lines',),
        unsettled_refusal='Only a posted stock receipt can be converted.',
    ),
    ConversionSource(
        source_type='input_tax_adjustment', label='Input tax adjustment',
        app_label='inventory', model_name='InputTaxAdjustment',
        date_field='adjustment_date',
        amounts=lambda row: (('tax_adjustment', row.tax_adjustment),),
        currency_path='receipt_line.receipt.currency_code',
        select_related=('receipt_line__receipt',),
    ),
    ConversionSource(
        source_type='supplier_invoice', label='Supplier invoice',
        app_label='purchasing', model_name='SupplierInvoice',
        date_field='invoice_date', amounts=_document_amounts,
        settled={'status': 'confirmed'},
        unsettled_refusal='Only a confirmed supplier invoice can be converted.',
    ),
    ConversionSource(
        source_type='supplier_payment', label='Supplier payment',
        app_label='purchasing', model_name='SupplierPayment',
        date_field='paid_on', amounts=_payment_amounts,
    ),
    ConversionSource(
        source_type='business_expense', label='Business expense',
        app_label='purchasing', model_name='BusinessExpense',
        date_field='incurred_on', amounts=_expense_amounts,
        settled={'status': 'confirmed'},
        unsettled_refusal='Only a confirmed business expense can be converted.',
    ),
)

SOURCES_BY_TYPE = {source.source_type: source for source in SOURCES}


def source_for(source_type):
    """Return the descriptor for a source type, or raise saying it is not one."""
    try:
        return SOURCES_BY_TYPE[source_type]
    except KeyError as exc:
        known = ', '.join(sorted(SOURCES_BY_TYPE))
        raise ValidationError({'source_type': (
            f'{source_type} is not a record a rate is recorded against. '
            f'Convertible records are: {known}.'
        )}) from exc


def live_conversions(workspace, pairs=None):
    """Return the live conversion for each converted row, keyed by its source.

    Live means nothing supersedes it. A superseded conversion is still in the
    table and still exported; it is simply not the rate a report reads.
    """
    rows = CurrencyConversion.objects.filter(
        workspace=workspace, superseded_by__isnull=True,
    )
    if pairs is not None:
        pairs = {(source_type, str(source_id)) for source_type, source_id in pairs}
        rows = rows.filter(source_type__in={pair[0] for pair in pairs})
    return {
        (row.source_type, row.source_id): row
        for row in rows
        if pairs is None or (row.source_type, row.source_id) in pairs
    }


def live_conversion(workspace, source_type, source_id):
    """Return the one live conversion of this row, or None."""
    return CurrencyConversion.objects.filter(
        workspace=workspace, source_type=source_type,
        source_id=str(source_id), superseded_by__isnull=True,
    ).first()


def converted_amounts(amounts, rate, direction):
    """Render one row's amounts and what each comes to, converted on its own."""
    return [
        {
            'name': name,
            'original': f'{Decimal(amount):.4f}',
            'converted': f'{convert_amount(amount, rate, direction):.4f}',
        }
        for name, amount in amounts
    ]


@transaction.atomic
def record_conversion(workspace, source_type, source_id, request, user=None):
    """Record the rate one transaction's amounts convert at.

    `request` carries what an operator typed: the rate, which way it is quoted,
    the method, where it came from and the date it applied on. Everything else
    is read off the row being converted, so a conversion cannot claim to
    convert an amount or a currency the row does not have.
    """
    source = source_for(source_type)
    row = source.rows(workspace).filter(pk=source_id).first()
    if row is None:
        missing = f'No {source.label.lower()} in this workspace has that id.'
        raise ValidationError({'source_id': source.unsettled_refusal or missing})
    currency = source.currency_of(row)
    method = request.get('method') or (
        ConversionMethod.BASE_CURRENCY if currency == workspace.currency_code
        else workspace.conversion_policy
    )
    rate = Decimal(request.get('rate', ONE))
    direction = request['quote_direction']
    # The record refuses this too, but the amounts are converted before the
    # record exists, and a rate of zero would fail there as a division rather
    # than as an answer naming the field it came from.
    if rate <= ZERO:
        raise ValidationError({'rate': 'A conversion rate must be above zero.'})
    earlier = live_conversion(workspace, source_type, source_id)
    supersedes = request.get('supersedes')
    if earlier is not None and supersedes is None:
        raise ValidationError({'supersedes': (
            'This record is already converted. Record the corrected rate as '
            'superseding the conversion it replaces.'
        )})
    if supersedes is not None and (earlier is None or earlier.pk != supersedes):
        raise ValidationError({'supersedes': (
            'That is not the conversion this record is currently read at.'
        )})
    return CurrencyConversion.objects.create(
        workspace=workspace,
        source_type=source_type,
        source_id=str(source_id),
        source_currency_code=currency,
        target_currency_code=workspace.currency_code,
        rate=rate,
        quote_direction=direction,
        method=method,
        rate_source=request.get('rate_source', ''),
        effective_date=request.get('effective_date') or source.date_of(row, workspace),
        converted_on=request.get('converted_on') or _today(workspace),
        amounts=converted_amounts(source.amounts(row), rate, direction),
        supersedes=earlier if supersedes is not None else None,
        reason=request.get('reason', ''),
        created_by=user,
    )


def _today(workspace):
    """The workspace-local date a conversion is being made on."""
    return timezone.now().astimezone(ZoneInfo(workspace.timezone)).date()


def backfill_identity_conversions(workspace, conversion_model=None, registry=global_apps):
    """Record the identity conversion of every row already in the base currency.

    It is not a conversion in any real sense -- a rate of one, applied to an
    amount that was already where it needed to be -- but recording it is what
    makes an unconverted row mean one thing. Without it a report reading a row
    with no conversion cannot tell an amount that never needed a rate from a
    foreign amount whose rate nobody has typed yet, and those are opposite
    answers.

    It skips any row that already carries a conversion, so it can be run again
    without recording a second live rate for anything. Amounts are written
    through `bulk_create`: every value on the record is built here rather than
    taken from a request, and a backfill that validated each row one at a time
    would be a query per row for nothing.
    """
    conversion_model = conversion_model or CurrencyConversion
    today = _today(workspace)
    converted = set(conversion_model.objects.filter(
        workspace=workspace,
    ).values_list('source_type', 'source_id'))
    pending = []
    for source in SOURCES:
        rows = source.rows(workspace, registry)
        if source.prefetch_related:
            rows = rows.prefetch_related(*source.prefetch_related)
        for row in rows:
            key = (source.source_type, str(row.pk))
            if key in converted or source.currency_of(row) != workspace.currency_code:
                continue
            pending.append(conversion_model(
                workspace=workspace,
                source_type=source.source_type,
                source_id=str(row.pk),
                source_currency_code=workspace.currency_code,
                target_currency_code=workspace.currency_code,
                rate=ONE,
                quote_direction=QuoteDirection.TARGET_PER_SOURCE,
                method=ConversionMethod.BASE_CURRENCY,
                rate_source=BASE_CURRENCY_SOURCE,
                effective_date=source.date_of(row, workspace),
                converted_on=today,
                amounts=converted_amounts(
                    source.amounts(row), ONE, QuoteDirection.TARGET_PER_SOURCE,
                ),
            ))
    conversion_model.objects.bulk_create(pending)
    return pending
