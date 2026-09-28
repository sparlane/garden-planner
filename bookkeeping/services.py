"""Transactional bookkeeping commands and reproducible income-year schedules."""

# Report assembly keeps each source schedule visible in one deterministic pass.
# pylint: disable=too-many-locals

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from billing.models import SupplyCorrection, SupplyDocument
from costing.models import CostAllocation
from inventory.models import InputTaxAdjustment, InventoryItem, StockLot, StockMovement, StockReceiptLine
from plantings.lifecycle import PRESENT_STATES, derive_state
from plantings.models import CohortEvent, CohortOperation, PlantCohort, PlantLifecycleEvent, SpecificPlant
from purchasing.models import BusinessExpense, SupplierInvoice, SupplierPayment
from sales.models import Payment, Refund

from .consolidation import ROUNDING_NOTE, Converter
from .conversion import backfill_identity_conversions
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
    ).exclude(item__category=InventoryItem.Category.TRAY).select_related('item')
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
    created.extend(_capture_plants(income_year, user, end))
    created.extend(_capture_cohorts(income_year, user, end))
    return created


#: What a line says when its stock was raised on inputs bought in more than one
#: currency. No exchange rate exists to combine them — `costing.currency` says
#: why one may not be invented here — so the line states no cost and is
#: provisional, rather than filing the sum of two currencies as a figure.
MIXED_CURRENCY_ASSUMPTION = (
    'Inputs were bought in {currencies} and no exchange rate exists to '
    'combine them, so no cost is stated.'
)


def _mixed_currency(held, assumptions):
    """Say whether this stock's cost is stateable, and note it where it is not."""
    if len(held) < 2:
        return False, assumptions
    return True, f'{assumptions} {MIXED_CURRENCY_ASSUMPTION.format(currencies=", ".join(sorted(held)))}'


def _line_currency(income_year, held):
    """Label a line with the currency its cost was actually recorded in.

    The workspace's own currency stands where there is no cost to contradict
    it, or where two of them leave nothing stated. A cost recorded wholly in
    one foreign currency is labelled with that one: relabelling it as the
    workspace's would file a euro figure as a dollar figure.
    """
    return next(iter(held)) if len(held) == 1 else income_year.workspace.currency_code


def _promoted_after(workspace, end):
    """Return the plants a promotion dated at or after `end` gave identities to.

    A promoted plant is germinated on the block's own observation date — the
    seed really did come up then — so it reads as a plant that was standing at
    the balance date. Where the promotion itself is dated after the balance
    date it was not: the unit was still part of the block, which is where
    `_capture_cohorts` puts it back, and capturing both would count the same
    seedling twice, once by name and once by number.

    The test is the promotion's date, so a promotion dated *before* the
    balance date keeps its plants whenever it happened to be typed. The cost
    side agrees: the promotion dates the layers it moved, so the block does not
    claim the unit's cost back either.
    """
    promoted = set()
    for payload in CohortOperation.objects.filter(
            workspace=workspace, action=CohortOperation.Action.PROMOTE,
            occurred_at__gte=end).values_list('payload', flat=True):
        promoted.update(payload.get('plants', ()))
    return promoted


def _capture_plants(income_year, user, end):
    """Freeze individual plants physically present at the balance instant."""
    # Filtered in Python rather than through `exclude(pk__in=...)`: two seasons
    # of promotions is a bind parameter each, and the rows are in hand anyway.
    promoted = _promoted_after(income_year.workspace, end)
    plants = [plant for plant in SpecificPlant.objects.filter(
        workspace=income_year.workspace, germinated__lt=end,
    ).select_related('batch__variety') if plant.pk not in promoted]
    events = defaultdict(list)
    for event in PlantLifecycleEvent.objects.filter(
            workspace=income_year.workspace, plant__in=plants,
            occurred_at__lt=end).order_by('occurred_at', 'pk'):
        events[event.plant_id].append(event)
    values = defaultdict(lambda: defaultdict(Decimal))
    unknown = set()
    for row in CostAllocation.objects.filter(
            specific_plant__in=plants, reversal_of=None,
            reversal__isnull=True).values('specific_plant_id', 'amount', 'currency_code'):
        if row['amount'] is None:
            unknown.add(row['specific_plant_id'])
        else:
            values[row['specific_plant_id']][row['currency_code']] += row['amount']
    rows = []
    for plant in plants:
        summary = derive_state(events[plant.pk])
        if summary.state not in PRESENT_STATES:
            continue
        held = values[plant.pk]
        mixed, assumptions = _mixed_currency(
            held, f'Lifecycle replay through year end: {summary.state}.',
        )
        value = None if mixed else money(sum(held.values(), ZERO))
        rows.append(StockValuationLine.objects.create(
            income_year=income_year,
            category=(StockValuationLine.Category.SALEABLE_PLANTS if summary.sellable else StockValuationLine.Category.WORK_IN_PROGRESS),
            description=f'{plant.batch.variety} — plant {plant.pk}',
            source_type='specific_plant', source_id=str(plant.pk),
            quantity=1, unit_code='unit', original_cost=value,
            method=StockValuationLine.Method.COST, value=value or ZERO,
            currency_code=_line_currency(income_year, held),
            assumptions=assumptions,
            derived=True, provisional=plant.pk in unknown or mixed,
            created_by=user,
        ))
    return rows


#: What a cohort line says when the subledger had not reached the block yet.
#: The units were standing there, so the line is captured and counted, but no
#: layer had been posted against the block by the balance date and inventing
#: one from a later run's figures would value year-end stock out of next year's
#: costs.
UNCOSTED_COHORT_ASSUMPTION = (
    'No cost layer stood against this block at the balance date, so its '
    'inputs were priced only afterwards and no cost is stated.'
)

#: A cohort line's standing assumption: both halves of it are reconstructions.
COHORT_ASSUMPTION = 'Cohort events replayed through year end, valued on the layers standing then.'

#: What a line says when the two halves disagree after all, because a fact
#: dated before year end was recorded after a later one. `_clamped_across`
#: says how that is detected and why the cost could not follow the count.
OUT_OF_ORDER_ASSUMPTION = (
    'A fact dated before year end was recorded after a later one, so the '
    'subledger could not move its cost back without valuing this block twice. '
    'The count reads the earlier date and the value does not.'
)


def _cohorts_at(workspace, end):
    """Return what each block's later events say it held at the balance instant.

    A block is read backwards from what it holds now, because a block whose
    own history predates the event log has no opening count to read forwards
    from. Every event dated at or after `end` is undone, so a unit sold, lost,
    promoted, split away, merged out, counted off or taken back in the spring
    goes back where it stood on the balance date. The date is
    `CohortOperation.occurred_at` — when the fact happened, not when it was
    typed — so a backdated loss or a corrected one lands in the year it
    belongs to, and an operation recorded late still counts against the year
    it names. The layers read the same date, so the two halves agree:
    `costing.services` dates a layer by the fact that moved it.

    The state returned beside the count is the `state_before` of the earliest
    of those events *in date order*, which is the state at `end` only where
    the block's operations were recorded in the order they happened. A `ready`
    dated 1 May typed before a `move` dated 10 April reads back as the May
    one's `state_before`, so a block that was growing in March can be filed
    under saleable plants. It picks the line's category and nothing else: no
    count and no figure depends on it.
    """
    later = {}
    for cohort_id, delta, state in CohortEvent.objects.filter(
            workspace=workspace, operation__occurred_at__gte=end,
    ).order_by('operation__occurred_at', 'pk').values_list(
            'cohort_id', 'quantity_delta', 'state_before'):
        moved, first = later.get(cohort_id, (0, state))
        later[cohort_id] = (moved + delta, first)
    return later


def _clamped_across(workspace, end):
    """Return the blocks whose cost was held back across the balance date.

    `costing.dating` will not date a layer before the one it supersedes, so
    two facts recorded out of the order they happened in file the earlier one
    under the later one's date. Usually that is harmless — both fall in the
    same year — but where the earlier fact is before `end` and the later one
    is at or after it, the cost it moved stays on the wrong side of the
    balance date while `_cohorts_at` moves the unit by the date it really
    carries. The block would then be counted after the fact and valued before
    it, which is the mismatch task 148's `_recorded_late` existed to stop,
    arriving by the other route.

    It is detectable because the run keeps the fact's own date. A *reversal*
    always carries the run's clamped date, so a reversal effective at or after
    `end` whose run was prompted by a fact before it is exactly a withdrawal
    the clamp pushed across the year. A posting is not a reliable witness — a
    first posting takes its source's date, which can straddle `end` for
    perfectly ordinary reasons — so only reversals are read.

    The posting-only case is deliberately left to fall where it does. A
    clamped run can give a block its *first* layer at or after `end` — a split
    dated 20 March typed after a 5 April sale lands the child's layer on 5
    April — and such a block then has no layer standing at the balance date at
    all, so it is `uncosted`, which is already counted, unvalued and
    provisional. Naming it here as well would flag it twice for one reason.

    There is no right figure to publish instead: the version that reflects the
    earlier fact and not the later one was never written. So the line keeps
    the as-at reading and is marked provisional, which is what task 148 did
    with the same disagreement and what holds the year open until somebody
    re-costs the batch.
    """
    return set(
        CostAllocation.objects
        .filter(
            plant_cohort__workspace=workspace,
            target_type=CostAllocation.TargetType.PLANT_COHORT,
            reversal_of__isnull=False,
            run__occurred_at__lt=end, effective_at__gte=end,
        )
        .values_list('plant_cohort_id', flat=True)
    )


def _group_layers(rows):
    """Collect `(cohort, amount, currency)` rows into a list per block."""
    layers = defaultdict(list)
    for cohort_id, amount, code in rows:
        layers[cohort_id].append((amount, code))
    return layers


def _cohort_layers_at(cohort_ids, end):
    """Group the standing cost layers each block carried at the balance instant.

    A layer is effective from the day the fact behind it happened until the day
    the fact that superseded it did, so the test is that pair: effective before
    `end`, and either never reversed or reversed only at or after it. Every way
    a block's cost can move works through one shape — the superseded layer is
    reversed and its replacement posted in the same run — so a spring sale, a
    sibling block's loss re-dividing a source, and a media application put on
    in April are all kept out of the closed year by the same test, without any
    of them having to be recognised.

    The date read is `CostAllocation.effective_at`, not the run's stamp, which
    is the same choice `_cohorts_at` makes on the count side and the reason the
    two halves now agree. A sale dated 20 March and typed in April takes its
    cost out of the block on 20 March, and media applied on 20 March and posted
    on 5 April is in the year it was applied in. A block with no layer at all
    is said out loud rather than filed as a zero.
    """
    return _group_layers(CostAllocation.objects.filter(
        plant_cohort_id__in=cohort_ids, target_type=CostAllocation.TargetType.PLANT_COHORT,
        reversal_of=None, effective_at__lt=end,
    ).filter(
        Q(reversal__isnull=True) | Q(reversal__effective_at__gte=end),
    ).values_list('plant_cohort_id', 'amount', 'currency_code'))


def _capture_cohorts(income_year, user, end):
    """Freeze each anonymous block as it stood at the balance instant.

    The `plant_cohort` column carries three parts of a block's cost: the stock
    still standing there (`PLANT_COHORT`), what already left with a customer
    (`COHORT_SALE`) and what died (`COHORT_LOSS`), told apart only by the
    target type. The count covers the first part, so the value has to be drawn
    from the same part; the others are cost of sale and production loss, and
    counting them here as well would raise profit by the amount they were meant
    to lower it.

    Neither the count nor the cost is read as it stands at capture. A year end
    is captured weeks after the balance date and spring sales carry on in the
    meantime, so the block is replayed back to `end` and valued on the layers
    that stood against it then: four units worth 1.0800 held on 31 March are
    captured as four worth 1.0800, whatever sold in September. Task 135's
    blanket flag over any block touched afterwards is gone with it.

    Both halves read the same date — the day the fact happened, never the day
    somebody typed it. The count reads `CohortOperation.occurred_at` and the
    value reads `CostAllocation.effective_at`, so a dispatch dated 20 March and
    recorded in April moves the unit and its cost together, out of the same
    year. Task 148 had to read such a block as it stood instead, a whole batch
    at a time; task 163 gave the layer its own date and that fallback is gone.

    They disagree in one case, and it is flagged rather than hidden: two facts
    recorded out of the order they happened in, straddling the balance date.
    `_clamped_across` finds those blocks and says what the ledger could not do
    about them.
    """
    workspace = income_year.workspace
    later = _cohorts_at(workspace, end)
    out_of_order = _clamped_across(workspace, end)
    cohorts = PlantCohort.objects.filter(workspace=workspace).filter(
        Q(quantity__gt=0) | Q(pk__in=list(later)),
    ).select_related('batch__variety')
    held_at_end = []
    for cohort in cohorts:
        moved, state = later.get(cohort.pk, (0, cohort.lifecycle_state))
        # A block first opened after `end` replays to nothing, which is what
        # keeps a split, a promotion or a customer return from being captured
        # as stock in a year it did not exist in.
        if cohort.quantity - moved > 0:
            held_at_end.append((cohort, cohort.quantity - moved, state))
    layers = _cohort_layers_at([cohort.pk for cohort, _quantity, _state in held_at_end], end)
    rows = []
    for cohort, quantity, state in held_at_end:
        standing = layers.get(cohort.pk, [])
        held = defaultdict(Decimal)
        unpriced = 0
        for amount, code in standing:
            if amount is None:
                unpriced += 1
            else:
                held[code] += amount
        uncosted = not standing
        unsettled = cohort.pk in out_of_order
        assumptions = COHORT_ASSUMPTION
        if uncosted:
            assumptions = f'{assumptions} {UNCOSTED_COHORT_ASSUMPTION}'
        if unsettled:
            assumptions = f'{assumptions} {OUT_OF_ORDER_ASSUMPTION}'
        mixed, assumptions = _mixed_currency(held, assumptions)
        # An uncosted block states no cost at all rather than a zero one, the
        # shape the capture already uses for an unpriced lot and for stock
        # raised in two currencies.
        value = None if mixed or uncosted else money(sum(held.values(), ZERO))
        rows.append(StockValuationLine.objects.create(
            income_year=income_year,
            category=(StockValuationLine.Category.SALEABLE_PLANTS if state == PlantCohort.LifecycleState.AVAILABLE else StockValuationLine.Category.WORK_IN_PROGRESS),
            description=f'{cohort.batch.variety} — cohort {cohort.pk}',
            source_type='plant_cohort', source_id=str(cohort.pk),
            quantity=quantity, unit_code='unit', original_cost=value,
            method=StockValuationLine.Method.COST, value=value or ZERO,
            currency_code=_line_currency(income_year, held),
            assumptions=assumptions,
            derived=True,
            provisional=unsettled or uncosted or bool(unpriced) or mixed,
            created_by=user,
        ))
    return rows


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
                    'rate_source_type': 'supplier_invoice', 'rate_source_id': invoice.pk,
                    'reference': invoice.external_reference,
                    'amount': str(line.deductible_amount), 'currency_code': invoice.currency_code,
                })
    else:
        payments = SupplierPayment.objects.filter(
            workspace=workspace, paid_on__gte=start, paid_on__lte=end,
            reversal_of=None, reversal__isnull=True,
        ).prefetch_related('allocations__invoice__lines')
        for payment in payments:
            for allocation in payment.allocations.all():
                invoice = allocation.invoice
                if invoice.total_incl_tax <= ZERO:
                    continue
                paid_ratio = allocation.amount / invoice.total_incl_tax
                for line in invoice.lines.exclude(expense_category=None):
                    rows.append({
                        'kind': 'paid_expense', 'date': payment.paid_on.isoformat(),
                        'source_type': 'supplier_payment', 'source_id': payment.pk,
                        'reference': payment.account_reference or payment.external_reference,
                        'amount': str(money(line.deductible_amount * paid_ratio)),
                        'currency_code': payment.currency_code,
                    })
    return rows


def _purchase_total(workspace, start, end, converter):
    """Total what was landed, each line at the rate of the receipt it came in on.

    A line is converted rather than the receipt's own stored equivalent,
    because the line is what this figure is the sum of; both use the same rate,
    which is what keeps them reconcilable to each other.
    """
    lines = StockReceiptLine.objects.filter(
        receipt__workspace=workspace, receipt__status='posted',
        receipt__received_date__gte=start, receipt__received_date__lte=end,
    ).select_related('receipt')
    return converter.total(
        ('stock_receipt', line.receipt_id, line.receipt.currency_code, line.acquisition_amount)
        for line in lines if line.acquisition_amount is not None
    )


def _stated(value):
    """Render a total, or None where one of its rows could not be converted.

    None here means exactly one thing: a figure that would have added up an
    amount nobody has typed a rate against. Stating it short would be a wrong
    number, and stating it across two currencies would be a different wrong
    number, so it states nothing and the data-quality finding says why.
    """
    return None if value is None else str(money(value))


def _opening_stock(prior):
    """Read what last year filed as its closing stock, if it could state one."""
    if prior is None:
        return ZERO
    value = prior.frozen_report.get('totals', {}).get('closing_stock')
    return None if value is None else Decimal(value)


def _stock_cost(opening, purchases, closing):
    """Opening plus purchases less closing, or nothing if a part is missing."""
    if None in (opening, purchases, closing):
        return None
    return opening + purchases - closing


def _working_result(earned, spent):
    """The year's result, which needs every one of its six parts.

    `earned` is what came in -- sales and other income -- and `spent` is what
    went against it: cost of sales, deductible expenses, depreciation, and the
    GST adjustments, which are signed and so are added rather than subtracted.
    """
    if None in earned or None in spent:
        return None
    cost_of_sales, expenses, depreciation, adjustments = spent
    return sum(earned) - cost_of_sales - expenses - depreciation + adjustments


def _stock_line_row(line, converter):
    """Render one closing-stock line, and what its value comes to converted."""
    return {
        'id': line.pk, 'category': line.category, 'description': line.description,
        'source_type': line.source_type, 'source_id': line.source_id,
        'quantity': str(line.quantity) if line.quantity is not None else None,
        'unit_code': line.unit_code, 'method': line.method, 'value': str(line.value),
        'currency_code': line.currency_code, 'evidence_url': line.evidence_url,
        'assumptions': line.assumptions,
        'converted_value': _stated(converter.amount(
            'stock_valuation_line', line.pk, line.currency_code, line.value,
        )),
        'converted_currency_code': converter.target,
    }


def build_report(income_year):
    """Build one source-linked schedule without mutating the income-year record."""
    start = year_start(income_year)
    end = income_year.year_end
    converter = Converter(income_year.workspace)
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
    stock_lines = list(income_year.stock_lines.all())
    closing = converter.total(
        ('stock_valuation_line', line.pk, line.currency_code, line.value)
        for line in stock_lines
    )
    prior = IncomeTaxYear.objects.filter(
        workspace=income_year.workspace,
        year_end=start - timedelta(days=1),
        status=IncomeTaxYear.Status.FINALIZED,
    ).order_by('-revision').first()
    opening = _opening_stock(prior)
    purchases = _purchase_total(income_year.workspace, start, end, converter)
    schedules = list(DepreciationSchedule.objects.filter(
        workspace=income_year.workspace, income_year_end=end,
    ).select_related('asset'))
    depreciation_rows = [{
        'kind': 'depreciation', 'date': end.isoformat(),
        'source_type': 'depreciation_schedule', 'source_id': row.pk,
        'rate_source_type': 'tax_asset', 'rate_source_id': row.asset_id,
        'reference': row.asset.code, 'amount': str(row.depreciation_claimed),
        'currency_code': row.asset.currency_code, 'asset_id': row.asset_id,
    } for row in schedules]
    depreciation = converter.row_total(depreciation_rows)
    gst_adjustments = converter.total(
        ('input_tax_adjustment', row.pk, row.receipt_line.receipt.currency_code, row.tax_adjustment)
        for row in InputTaxAdjustment.objects.filter(
            workspace=income_year.workspace,
            adjustment_date__gte=start, adjustment_date__lte=end,
        ).select_related('receipt_line__receipt')
    )
    sales_total = converter.row_total(sales)
    income_total = converter.row_total(other_income)
    expense_total = converter.row_total(expenses)
    cost_of_sales = _stock_cost(opening, purchases, closing)
    working_result = _working_result(
        (sales_total, income_total),
        (cost_of_sales, expense_total, depreciation, gst_adjustments),
    )
    # Rendered before the findings are read, because rendering is what puts
    # the rows through the converter -- including the cash reconciliation,
    # which has no total of its own but is still part of the year, and whose
    # missing rate is still part of the finding.
    rendered = [
        converter.converted_row(row)
        for row in sales + other_income + expenses + depreciation_rows
    ]
    rendered_cash = [converter.converted_row(row) for row in cash_reconciliation]
    rendered_stock = [_stock_line_row(line, converter) for line in stock_lines]
    quality = list(converter.findings())
    if opening is None:
        quality.append({
            'code': 'opening_stock_unstated', 'blocking': False,
            'message': (
                'The prior year could not state a closing stock, so this year '
                'has no opening stock to carry forward and every figure that '
                'would have used one states nothing. Recording the rates that '
                'year is waiting for settles both.'
            ),
        })
    provisional = sum(1 for line in stock_lines if line.provisional)
    if provisional:
        quality.append({'code': 'provisional_stock', 'count': provisional, 'blocking': True, 'message': 'Resolve every provisional stock value.'})
    if prior is None and opening == ZERO:
        quality.append({'code': 'opening_stock_unconfirmed', 'blocking': True, 'message': 'Confirm the opening stock value or prior finalized year before finalization.'})
    return {
        'report': 'income-tax-year', 'version': 'income-tax.v1',
        'income_year_id': income_year.pk, 'revision': income_year.revision,
        'basis': income_year.basis, 'date_from': start.isoformat(), 'date_to': end.isoformat(),
        'timezone': income_year.workspace.timezone, 'balance_date_assumption': '31 March',
        'currency_code': income_year.workspace.currency_code,
        'conversion': {
            'policy': income_year.workspace.conversion_policy,
            'methods': sorted(converter.methods),
            'complete': converter.complete,
            'rounding': ROUNDING_NOTE,
        },
        'totals': {
            'sales_ex_tax': _stated(sales_total), 'other_income': _stated(income_total),
            'opening_stock': _stated(opening), 'stock_purchases': _stated(purchases),
            'closing_stock': _stated(closing), 'cost_of_sales': _stated(cost_of_sales),
            'deductible_expenses': _stated(expense_total), 'depreciation': _stated(depreciation),
            'gst_adjustments': _stated(gst_adjustments),
            'working_result': _stated(working_result),
        },
        'rows': rendered,
        'cash_reconciliation': rendered_cash,
        'stock_lines': rendered_stock,
        'data_quality': quality,
    }


@transaction.atomic
def finalize_income_year(income_year, user, confirm_zero_opening=False):
    """Freeze a reconciled report; corrections are represented by a new revision."""
    income_year = IncomeTaxYear.objects.select_for_update().get(pk=income_year.pk)
    if income_year.status != IncomeTaxYear.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft income year can be finalized.'})
    # Every row already in the workspace's own currency gets its identity rate
    # record here, so the frozen year's export carries an exchange-rate record
    # against every transaction in it rather than only the foreign ones.
    backfill_identity_conversions(income_year.workspace)
    report = build_report(income_year)
    quality = report['data_quality']
    if confirm_zero_opening:
        quality = [row for row in quality if row['code'] != 'opening_stock_unconfirmed']
        report['data_quality'] = quality
        report['opening_stock_confirmed_zero'] = True
    # An unconverted row is reported, not blocked: a year can be filed with one,
    # showing as a finding. That is task 121's decision, and deliberately not
    # how provisional stock behaves one line above.
    blocking = [row for row in quality if row.get('blocking')]
    if blocking:
        raise ValidationError({'reconciliation': [row['message'] for row in blocking]})
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
    for row in report['rows']:
        if row.get('asset_id'):
            source_rows.append({'source_type': 'tax_asset', 'source_id': row['asset_id']})
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
