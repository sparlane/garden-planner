"""Transactional bookkeeping commands and reproducible income-year schedules."""

# Report assembly keeps each source schedule visible in one deterministic pass.
# pylint: disable=too-many-locals

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import SupplyCorrection, SupplyDocument
from inventory.models import InventoryItem, StockLot, StockMovement, StockReceiptLine
from purchasing.models import BusinessExpense, SupplierInvoice
from sales.models import Payment, Refund

from .models import (
    BookkeepingEntry,
    DepreciationSchedule,
    IncomeTaxYear,
    StockValuationLine,
    TaxRetentionRecord,
    LegalHoldEvent,
    ZERO,
)


MONEY_QUANTUM = Decimal('0.0001')


def money(value):
    """Round calculated money to the repository's ledger precision."""
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def year_start(income_year):
    """Return the inclusive start of a normal 31 March income year."""
    return income_year.year_end.replace(year=income_year.year_end.year - 1) + timedelta(days=1)


def _local_end(workspace, on_date):
    zone = ZoneInfo(workspace.timezone)
    return datetime.combine(on_date + timedelta(days=1), time.min, zone)


@transaction.atomic
def reverse_entry(entry, user, reason):
    """Reverse one live entry with an equal, traceable compensating entry."""
    entry = BookkeepingEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.reversal_of_id or hasattr(entry, 'reversal'):
        raise ValidationError({'entry': 'Only a live original entry can be reversed.'})
    return BookkeepingEntry.objects.create(
        workspace=entry.workspace,
        kind=entry.kind,
        occurred_on=timezone.localdate(),
        description=f'Reversal: {entry.description}',
        counterparty=entry.counterparty,
        liability=entry.liability,
        amount_ex_tax=entry.amount_ex_tax,
        tax_amount=entry.tax_amount,
        total_incl_tax=entry.total_incl_tax,
        tax_treatment=entry.tax_treatment,
        currency_code=entry.currency_code,
        account_reference=entry.account_reference,
        external_reference=entry.external_reference,
        evidence_url=entry.evidence_url,
        notes=reason,
        reversal_of=entry,
        created_by=user,
    )


def _stock_category(item):
    if item.category in {InventoryItem.Category.SEED, InventoryItem.Category.GROWING_MEDIA}:
        return StockValuationLine.Category.SEED_MEDIA
    if item.category == InventoryItem.Category.PACKAGING:
        return StockValuationLine.Category.PACKAGING
    return StockValuationLine.Category.OTHER


@transaction.atomic
def capture_inventory(income_year, user):
    """Replace derived lot rows with balances reconstructed at local year end."""
    income_year = IncomeTaxYear.objects.select_for_update().get(pk=income_year.pk)
    if income_year.status != IncomeTaxYear.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft income year can capture stock.'})
    income_year.stock_lines.filter(derived=True).delete()
    end = _local_end(income_year.workspace, income_year.year_end)
    balances = defaultdict(Decimal)
    movements = StockMovement.objects.filter(
        workspace=income_year.workspace, occurred_at__lt=end,
    ).values('lot_id', 'quantity', 'source_id', 'destination_id')
    for row in movements:
        if row['source_id']:
            balances[row['lot_id']] -= row['quantity']
        if row['destination_id']:
            balances[row['lot_id']] += row['quantity']
    lots = StockLot.objects.filter(
        workspace=income_year.workspace, pk__in=[key for key, value in balances.items() if value > 0],
    ).select_related('item')
    created = []
    for lot in lots:
        quantity = balances[lot.pk]
        value = None if lot.base_unit_cost is None else money(quantity * lot.base_unit_cost)
        created.append(StockValuationLine.objects.create(
            income_year=income_year,
            category=_stock_category(lot.item),
            description=f'{lot.item.name} — {lot.identifier}',
            source_type='stock_lot',
            source_id=str(lot.pk),
            quantity=quantity,
            unit_code=lot.item.base_unit,
            original_cost=value,
            method=StockValuationLine.Method.COST,
            value=value or ZERO,
            currency_code=lot.currency_code,
            assumptions='Derived from immutable stock movements through the workspace-local year end.',
            derived=True,
            provisional=value is None or lot.quantity_certainty != 'exact',
            created_by=user,
        ))
    return created


def _signed_correction(correction):
    sign = Decimal('-1') if correction.correction_type == SupplyCorrection.CorrectionType.CREDIT else Decimal('1')
    return sign * correction.subtotal_ex_tax


def _accrual_sales(workspace, start, end):
    documents = SupplyDocument.objects.filter(
        workspace=workspace, issued_on__gte=start, issued_on__lte=end,
    )
    corrections = SupplyCorrection.objects.filter(
        workspace=workspace, corrected_on__gte=start, corrected_on__lte=end,
    )
    rows = [{
        'kind': 'sale', 'date': row.issued_on.isoformat(), 'source_type': 'supply_document',
        'source_id': row.pk, 'reference': row.document_number,
        'amount': str(row.subtotal_ex_tax), 'currency_code': row.currency_code,
    } for row in documents]
    rows.extend({
        'kind': 'sale_correction', 'date': row.corrected_on.isoformat(),
        'source_type': 'supply_correction', 'source_id': row.pk,
        'reference': row.document_number, 'amount': str(_signed_correction(row)),
        'currency_code': row.currency_code,
    } for row in corrections)
    return rows


def _cash_sales(workspace, start, end):
    rows = []
    for model, date_field, kind, sign in (
            (Payment, 'paid_on', 'cash_sale', Decimal('1')),
            (Refund, 'refunded_at__date', 'cash_refund', Decimal('-1'))):
        queryset = model.objects.filter(
            workspace=workspace, reversal_of=None, reversal__isnull=True,
            **{f'{date_field}__gte': start, f'{date_field}__lte': end},
        ).select_related('order')
        for row in queryset:
            total = row.order.total_incl_tax
            ex_tax_ratio = row.order.subtotal_ex_tax / total if total else ZERO
            occurred = row.paid_on if model is Payment else row.refunded_at.date()
            rows.append({
                'kind': kind, 'date': occurred.isoformat(),
                'source_type': model._meta.model_name, 'source_id': row.pk,
                'reference': row.external_reference if model is Payment else '',
                'amount': str(money(sign * row.amount * ex_tax_ratio)),
                'currency_code': row.currency_code,
            })
    return rows


def _expense_rows(income_year, start, end):
    workspace = income_year.workspace
    rows = []
    if income_year.basis == IncomeTaxYear.Basis.ACCRUAL:
        expenses = BusinessExpense.objects.filter(
            workspace=workspace, status=BusinessExpense.Status.CONFIRMED,
            incurred_on__gte=start, incurred_on__lte=end,
        )
    else:
        expenses = BusinessExpense.objects.filter(
            workspace=workspace, status=BusinessExpense.Status.CONFIRMED,
            paid_on__gte=start, paid_on__lte=end, supplier_invoice=None,
        )
    for expense in expenses:
        rows.append({
            'kind': 'expense', 'date': expense.incurred_on.isoformat(),
            'source_type': 'business_expense', 'source_id': expense.pk,
            'reference': expense.account_reference, 'amount': str(expense.deductible_amount),
            'currency_code': expense.currency_code,
        })
    if income_year.basis == IncomeTaxYear.Basis.ACCRUAL:
        invoices = SupplierInvoice.objects.filter(
            workspace=workspace, status=SupplierInvoice.Status.CONFIRMED,
            invoice_date__gte=start, invoice_date__lte=end,
        ).prefetch_related('lines')
        for invoice in invoices:
            for line in invoice.lines.exclude(expense_category=None):
                rows.append({
                    'kind': 'expense', 'date': invoice.invoice_date.isoformat(),
                    'source_type': 'supplier_invoice_line', 'source_id': line.pk,
                    'reference': invoice.external_reference,
                    'amount': str(line.deductible_amount), 'currency_code': invoice.currency_code,
                })
    return rows


def _purchase_total(workspace, start, end):
    lines = StockReceiptLine.objects.filter(
        receipt__workspace=workspace, receipt__status='posted',
        receipt__received_date__gte=start, receipt__received_date__lte=end,
    )
    return sum((line.acquisition_amount for line in lines if line.acquisition_amount is not None), ZERO)


def build_report(income_year):
    """Build one source-linked schedule without mutating the income-year record."""
    start = year_start(income_year)
    end = income_year.year_end
    sales = _accrual_sales(income_year.workspace, start, end) if income_year.basis == IncomeTaxYear.Basis.ACCRUAL else _cash_sales(income_year.workspace, start, end)
    expenses = _expense_rows(income_year, start, end)
    entries = BookkeepingEntry.objects.filter(
        workspace=income_year.workspace, occurred_on__gte=start, occurred_on__lte=end,
        reversal_of=None, reversal__isnull=True,
    )
    other_income = [{
        'kind': row.kind, 'date': row.occurred_on.isoformat(),
        'source_type': 'bookkeeping_entry', 'source_id': row.pk,
        'reference': row.external_reference, 'amount': str(row.amount_ex_tax),
        'currency_code': row.currency_code,
    } for row in entries.filter(kind=BookkeepingEntry.Kind.OTHER_INCOME)]
    cash_reconciliation = [{
        'kind': row.kind, 'date': row.occurred_on.isoformat(),
        'source_type': 'bookkeeping_entry', 'source_id': row.pk,
        'reference': row.external_reference, 'amount': str(row.total_incl_tax),
        'currency_code': row.currency_code,
    } for row in entries.exclude(kind=BookkeepingEntry.Kind.OTHER_INCOME)]
    closing = sum((line.value for line in income_year.stock_lines.all()), ZERO)
    prior = IncomeTaxYear.objects.filter(
        workspace=income_year.workspace,
        year_end=start - timedelta(days=1),
        status=IncomeTaxYear.Status.FINALIZED,
    ).order_by('-revision').first()
    opening = Decimal(prior.frozen_report.get('totals', {}).get('closing_stock', '0')) if prior else ZERO
    purchases = money(_purchase_total(income_year.workspace, start, end))
    depreciation = DepreciationSchedule.objects.filter(
        workspace=income_year.workspace, income_year_end=end,
    ).aggregate(total=Sum('depreciation_claimed'))['total'] or ZERO
    sales_total = sum((Decimal(row['amount']) for row in sales), ZERO)
    income_total = sum((Decimal(row['amount']) for row in other_income), ZERO)
    expense_total = sum((Decimal(row['amount']) for row in expenses), ZERO)
    cost_of_sales = opening + purchases - closing
    currencies = sorted({
        row['currency_code'] for row in sales + expenses + other_income + cash_reconciliation
    } | {line.currency_code for line in income_year.stock_lines.all()})
    quality = []
    if len(currencies) > 1 or (currencies and currencies != [income_year.workspace.currency_code]):
        quality.append({'code': 'unsupported_currency', 'message': 'Task 121 must convert every source to the workspace currency before finalization.'})
    provisional = income_year.stock_lines.filter(provisional=True).count()
    if provisional:
        quality.append({'code': 'provisional_stock', 'count': provisional, 'message': 'Resolve every provisional stock value.'})
    if prior is None and opening == ZERO:
        quality.append({'code': 'opening_stock_unconfirmed', 'message': 'Confirm the opening stock value or prior finalized year before finalization.'})
    return {
        'report': 'income-tax-year', 'version': 'income-tax.v1',
        'income_year_id': income_year.pk, 'revision': income_year.revision,
        'basis': income_year.basis, 'date_from': start.isoformat(), 'date_to': end.isoformat(),
        'timezone': income_year.workspace.timezone, 'balance_date_assumption': '31 March',
        'currency_code': income_year.workspace.currency_code,
        'totals': {
            'sales_ex_tax': str(money(sales_total)), 'other_income': str(money(income_total)),
            'opening_stock': str(money(opening)), 'stock_purchases': str(purchases),
            'closing_stock': str(money(closing)), 'cost_of_sales': str(money(cost_of_sales)),
            'deductible_expenses': str(money(expense_total)), 'depreciation': str(money(depreciation)),
            'working_result': str(money(sales_total + income_total - cost_of_sales - expense_total - depreciation)),
        },
        'rows': sales + other_income + expenses,
        'cash_reconciliation': cash_reconciliation,
        'stock_lines': [{
            'id': line.pk, 'category': line.category, 'description': line.description,
            'source_type': line.source_type, 'source_id': line.source_id,
            'quantity': str(line.quantity) if line.quantity is not None else None,
            'unit_code': line.unit_code, 'method': line.method, 'value': str(line.value),
            'currency_code': line.currency_code, 'evidence_url': line.evidence_url,
            'assumptions': line.assumptions,
        } for line in income_year.stock_lines.all()],
        'data_quality': quality,
    }


@transaction.atomic
def finalize_income_year(income_year, user, confirm_zero_opening=False):
    """Freeze a reconciled report; corrections are represented by a new revision."""
    income_year = IncomeTaxYear.objects.select_for_update().get(pk=income_year.pk)
    if income_year.status != IncomeTaxYear.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft income year can be finalized.'})
    report = build_report(income_year)
    quality = report['data_quality']
    if confirm_zero_opening:
        quality = [row for row in quality if row['code'] != 'opening_stock_unconfirmed']
        report['data_quality'] = quality
        report['opening_stock_confirmed_zero'] = True
    if quality:
        raise ValidationError({'reconciliation': [row['message'] for row in quality]})
    IncomeTaxYear.objects.filter(pk=income_year.pk).update(
        status=IncomeTaxYear.Status.FINALIZED,
        frozen_report=report,
        finalized_at=timezone.now(),
        finalized_by=user,
    )
    source_rows = report['rows'] + report['cash_reconciliation']
    source_rows.extend({
        'source_type': row['source_type'], 'source_id': row['source_id']
    } for row in report['stock_lines'])
    source_rows.append({'source_type': 'income_tax_year', 'source_id': income_year.pk})
    for row in source_rows:
        TaxRetentionRecord.objects.get_or_create(
            workspace=income_year.workspace,
            source_type=row['source_type'], source_id=str(row['source_id']),
            defaults={
                'income_year_end': income_year.year_end,
                'retain_until': income_year.retain_until,
                'reason': f'Included in income-tax year revision {income_year.revision}.',
                'created_by': user,
            },
        )
    income_year.refresh_from_db()
    return income_year


@transaction.atomic
def set_legal_hold(retention, active, reason, user):
    """Record and apply a legal-hold state change without erasing its history."""
    retention = TaxRetentionRecord.objects.select_for_update().get(pk=retention.pk)
    if retention.legal_hold == active:
        raise ValidationError({'active': 'The retained record already has that hold state.'})
    event = LegalHoldEvent.objects.create(
        workspace=retention.workspace, retention=retention,
        active=active, reason=reason, created_by=user,
    )
    TaxRetentionRecord.objects.filter(pk=retention.pk).update(legal_hold=active)
    return event
