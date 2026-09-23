"""Record the identity conversion of every row already in the base currency.

What happens to existing rows: a settled row whose currency is its workspace's
own gains one `CurrencyConversion` at a rate of one, with the method
`base_currency` and its own business date as the effective date. Nothing on the
row itself is touched, and no amount changes -- a rate of one applied to an
amount that was already in the right currency is the amount. A row in any other
currency gains nothing and is reported by the GST and income-tax reports as
awaiting a rate.

Draft receipts, draft supplier invoices and draft expenses are skipped, because
their amounts can still move and a stored equivalent of an amount that then
moves is worse than none. They are converted when they are settled.

The source list is frozen here rather than read from `bookkeeping.conversion`:
a migration has to keep doing what it did on the day it was written, and a
source added to that module later belongs to whatever migration adds it.

Reversing this drops the identity conversions and keeps any real rate that has
been typed since, which is the only part a later run could not rebuild.
"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


ZERO = Decimal('0')
ONE = Decimal('1')
BASE_CURRENCY = 'base_currency'
BASE_CURRENCY_SOURCE = 'Already in the workspace currency'
TARGET_PER_SOURCE = 'target_per_source'


def _document_amounts(row):
    return (
        ('subtotal_ex_tax', row.subtotal_ex_tax),
        ('tax_total', row.tax_total),
        ('total_incl_tax', row.total_incl_tax),
    )


def _expense_amounts(row):
    return _document_amounts(row) + (
        ('recoverable_tax', row.recoverable_tax),
        ('deductible_amount', row.deductible_amount),
    )


def _payment_amounts(row):
    return (('amount', row.amount),)


def _receipt_amounts(row):
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


#: (source type, app, model, date field, amounts, currency path, filters)
SOURCES = (
    ('supply_document', 'billing', 'SupplyDocument', 'issued_on', _document_amounts, (), {}),
    ('supply_correction', 'billing', 'SupplyCorrection', 'corrected_on', _document_amounts, (), {}),
    ('payment', 'sales', 'Payment', 'paid_on', _payment_amounts, (), {}),
    ('refund', 'sales', 'Refund', 'refunded_at', _payment_amounts, (), {}),
    ('stock_receipt', 'inventory', 'StockReceipt', 'received_date', _receipt_amounts, (), {'status': 'posted'}),
    (
        'input_tax_adjustment', 'inventory', 'InputTaxAdjustment', 'adjustment_date',
        lambda row: (('tax_adjustment', row.tax_adjustment),),
        ('receipt_line', 'receipt', 'currency_code'), {},
    ),
    ('supplier_invoice', 'purchasing', 'SupplierInvoice', 'invoice_date', _document_amounts, (), {'status': 'confirmed'}),
    ('supplier_payment', 'purchasing', 'SupplierPayment', 'paid_on', _payment_amounts, (), {}),
    ('business_expense', 'purchasing', 'BusinessExpense', 'incurred_on', _expense_amounts, (), {'status': 'confirmed'}),
)


def _currency_of(row, path):
    value = row
    for step in path or ('currency_code',):
        value = getattr(value, step)
    return value


def _date_of(row, field, zone):
    value = getattr(row, field)
    return value.astimezone(zone).date() if hasattr(value, 'astimezone') else value


def record_identity_conversions(apps, schema_editor):
    """Give every settled base-currency row its rate of one."""
    conversion_model = apps.get_model('bookkeeping', 'CurrencyConversion')
    workspace_model = apps.get_model('workspaces', 'Workspace')
    for workspace in workspace_model.objects.all():
        zone = ZoneInfo(workspace.timezone)
        today = timezone.now().astimezone(zone).date()
        pending = []
        for source_type, app_label, model_name, date_field, amounts, path, filters in SOURCES:
            rows = apps.get_model(app_label, model_name).objects.filter(
                workspace=workspace, **filters,
            )
            for row in rows:
                if _currency_of(row, path) != workspace.currency_code:
                    continue
                pending.append(conversion_model(
                    workspace=workspace,
                    source_type=source_type,
                    source_id=str(row.pk),
                    source_currency_code=workspace.currency_code,
                    target_currency_code=workspace.currency_code,
                    rate=ONE,
                    quote_direction=TARGET_PER_SOURCE,
                    method=BASE_CURRENCY,
                    rate_source=BASE_CURRENCY_SOURCE,
                    effective_date=_date_of(row, date_field, zone),
                    converted_on=today,
                    amounts=[
                        {
                            'name': name,
                            'original': f'{Decimal(amount):.4f}',
                            'converted': f'{Decimal(amount):.4f}',
                        }
                        for name, amount in amounts(row)
                    ],
                ))
        conversion_model.objects.bulk_create(pending, batch_size=500)


def drop_identity_conversions(apps, schema_editor):
    """Remove what this migration recorded, and nothing anybody typed."""
    conversion_model = apps.get_model('bookkeeping', 'CurrencyConversion')
    conversion_model.objects.filter(method=BASE_CURRENCY).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('bookkeeping', '0003_currency_conversion'),
        ('billing', '0001_issue_supply_documents'),
        ('sales', '0014_salesreturnline_container_fill_fulfillmentcontainer'),
        ('inventory', '0017_alter_stocktaketarget_target_type'),
        ('purchasing', '0004_expense_category_catalog'),
    ]

    operations = [
        migrations.RunPython(record_identity_conversions, drop_identity_conversions),
    ]
