"""Which records carry a rate, and how one is recorded against them.

`workspaces.conversion` says how a rate converts an amount. This says what a
rate is recorded *against*: twelve kinds of row across four apps, each named by
a `source_type` that matches the one the GST entries and the income-year
schedules already use, so a conversion found here is the conversion of the row
a report is looking at rather than of something with a similar name. Between
them they cover every row either return derives a figure from -- a report that
found one it could not offer a rate for would be reporting a problem nobody
could fix.

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
from django.db import IntegrityError, transaction
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
    workspace_lookup: str = 'workspace'
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
            **{self.workspace_lookup: workspace}, **self.settled,
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
        value = row
        for step in self.date_field.split('.'):
            value = getattr(value, step)
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


def _valuation_amounts(row):
    """A closing-stock line's value, and the cost it was measured against."""
    amounts = [('value', row.value)]
    if row.original_cost is not None:
        amounts.append(('original_cost', row.original_cost))
    return tuple(amounts)


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
#:
#: The closing-stock line is the one entry whose amounts can still move -- a
#: valuation line is editable while its income year is a draft, and re-running
#: the capture replaces the derived ones outright. It is here anyway, because
#: the alternative is an income year holding one foreign valuation line that
#: can never state a closing stock. A line replaced by a later capture is a new
#: row needing a new rate, which is the right answer; and every reader converts
#: the value the line carries now at the rate that was typed, so a line edited
#: after conversion is reported correctly even though the record's stored
#: snapshot is of the earlier figure.
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
        source_type='bookkeeping_entry', label='Bookkeeping entry',
        app_label='bookkeeping', model_name='BookkeepingEntry',
        date_field='occurred_on',
        amounts=lambda row: (
            ('amount_ex_tax', row.amount_ex_tax),
            ('tax_amount', row.tax_amount),
            ('total_incl_tax', row.total_incl_tax),
        ),
    ),
    ConversionSource(
        source_type='tax_asset', label='Depreciable asset',
        app_label='bookkeeping', model_name='TaxAsset',
        date_field='acquired_on',
        amounts=lambda row: (
            ('cost_incl_tax', row.cost_incl_tax),
            ('recoverable_tax', row.recoverable_tax),
            ('tax_cost', row.tax_cost),
        ),
    ),
    ConversionSource(
        source_type='stock_valuation_line', label='Closing stock line',
        app_label='bookkeeping', model_name='StockValuationLine',
        date_field='income_year.year_end', amounts=_valuation_amounts,
        workspace_lookup='income_year__workspace',
        select_related=('income_year',),
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


def _convertible_row(workspace, source, source_id):
    """Find the record a rate is being recorded against, or say why not.

    Three different answers, because they are three different situations and
    an operator can only act on the one they are in: the id is not an id, no
    such record exists here, or the record exists but its amounts can still
    move. `source_id` is a character column on the conversion, so an id that is
    not a number reaches the query as text and raises rather than not matching
    -- caught here so it answers as a refused field rather than as a fault.
    """
    if not str(source_id).isdigit():
        raise ValidationError({'source_id': (
            f'A {source.label.lower()} is identified by a number, and '
            f'"{source_id}" is not one.'
        )})
    row = source.rows(workspace).filter(pk=source_id).first()
    if row is not None:
        return row
    if source.settled and source.model().objects.filter(
        **{source.workspace_lookup: workspace}, pk=source_id,
    ).exists():
        raise ValidationError({'source_id': source.unsettled_refusal})
    raise ValidationError({'source_id': (
        f'No {source.label.lower()} in this workspace has that id.'
    )})


def live_conversions(workspace, pairs=None):
    """Return the live conversion for each converted row, keyed by its source.

    Live means nothing supersedes it. A superseded conversion is still in the
    table and still exported; it is simply not the rate a report reads.

    A record can only have one, which the database now refuses to let go
    otherwise. The ordering is here so that this and `live_conversion` below
    would answer the same on a workspace migrated before that constraint
    existed: the newest wins, because a reader disagreeing with the writer
    about which rate is current is worse than either answer.
    """
    rows = CurrencyConversion.objects.filter(
        workspace=workspace, superseded_by__isnull=True,
    ).order_by('pk')
    if pairs is not None:
        pairs = {(source_type, str(source_id)) for source_type, source_id in pairs}
        rows = rows.filter(source_type__in={pair[0] for pair in pairs})
    return {
        (row.source_type, row.source_id): row
        for row in rows
        if pairs is None or (row.source_type, row.source_id) in pairs
    }


def live_conversion(workspace, source_type, source_id, lock=False):
    """Return the one live conversion of this row, or None.

    `lock` takes the row for update, so a correction reads the rate it is
    replacing under the same lock it replaces it under. It locks this row
    only: `of=('self',)` keeps the lock off the nullable join to the
    conversion this one supersedes.
    """
    rows = CurrencyConversion.objects.filter(
        workspace=workspace, source_type=source_type,
        source_id=str(source_id), superseded_by__isnull=True,
    ).order_by('-pk')
    if lock:
        rows = rows.select_for_update(of=('self',))
    return rows.first()


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
    row = _convertible_row(workspace, source, source_id)
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
    if currency == workspace.currency_code and rate != ONE:
        raise ValidationError({'source_currency_code': (
            f'This {source.label.lower()} is already in '
            f'{workspace.currency_code}, so there is nothing to convert. Such '
            f'a record carries the base-currency conversion, at a rate of one.'
        )})
    earlier = live_conversion(workspace, source_type, source_id, lock=True)
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
    fields = {
        'workspace': workspace,
        'source_type': source_type,
        'source_id': str(source_id),
        'source_currency_code': currency,
        'target_currency_code': workspace.currency_code,
        'rate': rate,
        'quote_direction': direction,
        'method': method,
        'rate_source': request.get('rate_source', ''),
        'effective_date': request.get('effective_date') or source.date_of(row, workspace),
        'converted_on': request.get('converted_on') or _today(workspace),
        'amounts': converted_amounts(source.amounts(row), rate, direction),
        'supersedes': earlier if supersedes is not None else None,
        'reason': request.get('reason', ''),
        'created_by': user,
    }
    try:
        # Its own savepoint, so a refused write leaves the surrounding
        # transaction usable enough to answer with why.
        with transaction.atomic():
            return CurrencyConversion.objects.create(**fields)
    except IntegrityError as exc:
        # The live-rate constraint: another request recorded the first
        # conversion of this record between the read above and this write, so
        # what this one is is a correction that has not said what it corrects.
        raise ValidationError({'supersedes': (
            'This record was converted while this rate was being recorded. '
            'Record the corrected rate as superseding the conversion it '
            'replaces.'
        )}) from exc


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
