"""GST period totals and the immutable source rows behind every figure.

Two reports. `gst_period_report` is one row per taxable period, with the boxes
a New Zealand GST return is filed from. `gst_entry_report` is the drill-down:
one row per derived entry, which is what every period total is the sum of and
what makes the phrase "reconciles to immutable source records" checkable rather
than asserted.

This is deliberately not the same recognition as `reporting.commerce`. That
report answers what a period earned, and recognises on fulfillment. This one
answers what a period owes, and recognises on the basis in force at each time
of supply. For one order paid in one period and delivered in the next, the two
disagree — and they are supposed to. The `reconciliation` block says so in the
payload rather than leaving somebody to discover it against a filed return.

Nothing here is totalled across currencies. There is no exchange rate in this
application (task 121 owns that), so a period trading in two currencies reports
each separately and withholds the consolidated net figure, exactly as
`profitability_report` withholds a margin it cannot state.
"""

# pylint: disable=duplicate-code

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from tax.entries import AWAITING_PAYMENT, PURCHASE, derive_entries
from tax.periods import (
    enumerate_periods,
    local_date,
    registration_history,
    taxable_period_for,
)
from tax.recognition import SUPPLY, SUPPLY_CREDIT
from tax.services import closures_by_label
from tax.transition import basis_transitions
from tax.turnover import registration_warnings

from .common import Report, decimal_string


MONEY_PLACES = 4
ZERO = Decimal('0.0000')

PERIOD_COLUMNS = (
    'period_label', 'period_start', 'period_end', 'clipped',
    'basis', 'filing_frequency', 'gst_number', 'registration',
    'taxable_supplies_incl_tax', 'zero_rated_supplies', 'exempt_supplies',
    'unclassified_supplies', 'supply_credits_incl_tax',
    'output_tax', 'debit_adjustments', 'total_output_tax',
    'purchases_incl_tax', 'input_tax', 'credit_adjustments', 'total_input_tax',
    'non_recoverable_tax', 'input_tax_awaiting_payment',
    'net_gst', 'net_gst_direction', 'entry_count', 'currency_code',
    'filed', 'filed_at', 'filed_total_drift',
)

ENTRY_COLUMNS = (
    'period_label', 'kind', 'supply_date', 'basis', 'source_type', 'source_id',
    'document_id', 'line_id', 'tax_code', 'tax_rate',
    'taxable', 'tax', 'non_recoverable_tax', 'gross',
    'currency_code', 'time_of_supply_source', 'proxy', 'exclusion',
)

RECONCILIATION = {
    'supplies_equation': (
        'total supplies = taxable supplies + zero-rated supplies '
        '- supply credits'
    ),
    'output_equation': 'total output tax = output tax + debit adjustments',
    'input_equation': 'total input tax = input tax + credit adjustments',
    'net_equation': 'net GST = total output tax - total input tax',
    'entry_equation': (
        'every period total is the sum of its entries in this report, and '
        'every entry is derived from one immutable commerce record'
    ),
    'amount_equation': 'gross = taxable + claimable tax + non-claimable tax',
    'recognition_note': (
        'GST recognition uses the basis in force at each time of supply; '
        'profitability recognition uses fulfillment dates and is calculated '
        'separately. The two legitimately disagree about which period an '
        'order belongs to.'
    ),
    'proxy_note': (
        'A fulfillment stands in for the invoice date the invoice basis needs; '
        'entries relying on it are marked proxy.'
    ),
}

#: Explanations for the exclusion reasons an entry can carry, so a report says
#: what a missing figure means rather than leaving a total looking complete.
EXCLUSION_MESSAGES = {
    'no_registration': (
        'Commerce was recorded on dates before any GST registration. It '
        'carried no GST obligation and belongs to no return period.'
    ),
    'deregistered_gap': (
        'Commerce was recorded on dates after a cessation and before any later '
        'registration, so it belongs to no return period.'
    ),
    AWAITING_PAYMENT: (
        'Under the payments and hybrid bases input tax is claimed when the '
        'supplier is paid, and no supplier payment date is recorded anywhere '
        'yet. These purchases are held back rather than claimed on their '
        'receipt date.'
    ),
}


def gst_period_report(workspace, filters):
    """Return one row per taxable period, in the shape a GST return is filed in."""
    start, end = _date_bounds(workspace, filters)
    entries = derive_entries(workspace, start, end)
    periods = enumerate_periods(workspace, start, end, history=registration_history(workspace))
    closures = closures_by_label(workspace)
    rows = []
    for period in periods:
        rows.extend(_mark_filed(row, closures) for row in _period_rows(period, entries))
    return Report(
        name='gst-periods',
        filters=dict(filters),
        columns=PERIOD_COLUMNS,
        rows=rows,
        totals=_period_totals(rows, basis_transitions(workspace)),
        reconciliation=dict(RECONCILIATION),
        data_quality=_data_quality(workspace, entries, rows, end),
    )


def _mark_filed(row, closures):
    """Say whether a period was filed, and whether re-reading it still agrees.

    A derived report follows a late correction automatically, which is right
    everywhere except a period already reported to Inland Revenue. Comparing
    what is derived now against what was filed is how that stops being
    invisible.
    """
    closure = closures.get(row['period_label'])
    if closure is None:
        return {**row, 'filed': False, 'filed_at': None, 'filed_total_drift': None}
    filed = closure.filed_totals.get(row['currency_code'], {}).get('net_gst')
    drift = (
        None if filed is None
        else decimal_string(Decimal(row['net_gst']) - Decimal(filed), MONEY_PLACES)
    )
    return {
        **row,
        'filed': True,
        'filed_at': closure.created.isoformat(),
        'filed_total_drift': drift,
    }


def gst_entry_report(workspace, filters):
    """Return every derived entry, which is what each period total is made of."""
    start, end = _date_bounds(workspace, filters)
    entries = _apply_entry_filters(derive_entries(workspace, start, end), filters)
    return Report(
        name='gst-entries',
        filters=dict(filters),
        columns=ENTRY_COLUMNS,
        rows=[_entry_row(entry) for entry in entries],
        totals=_entry_totals(entries),
        reconciliation=dict(RECONCILIATION),
        data_quality=[],
    )


def _apply_entry_filters(entries, filters):
    """Narrow the drill-down to the slice a period total was queried about."""
    period = filters.get('period')
    kind = filters.get('kind')
    tax_code = filters.get('tax_code')
    exclusion = filters.get('exclusion')
    selected = []
    for entry in entries:
        if period and entry.period_label != period:
            continue
        if kind and entry.kind != kind:
            continue
        if tax_code and entry.tax_code != tax_code:
            continue
        if exclusion and entry.exclusion != exclusion:
            continue
        selected.append(entry)
    return selected


def _period_rows(period, entries):
    """Return one row per currency this period traded in, or one empty row.

    A period with no trading still gets a row. A return was due for it, and a
    report that simply omitted it would look the same as one where the period
    had been forgotten.
    """
    matching = [
        entry for entry in entries
        if entry.period is not None and entry.period.label == period.label
    ]
    # Held-back input tax has no period, but it was incurred inside this one and
    # the operator needs to see how much a return is not claiming.
    awaiting = [
        entry for entry in entries
        if entry.exclusion == AWAITING_PAYMENT and period.contains(entry.supply_date)
    ]
    by_currency = defaultdict(lambda: ([], []))
    for entry in matching:
        by_currency[entry.currency_code][0].append(entry)
    for entry in awaiting:
        by_currency[entry.currency_code][1].append(entry)
    if not by_currency:
        return [_period_row(period, '', [], [])]
    return [
        _period_row(period, currency, included, held)
        for currency, (included, held) in sorted(by_currency.items())
    ]


def _period_row(period, currency_code, entries, awaiting):  # pylint: disable=too-many-locals
    """Sum one period's entries into the boxes a return is filed from."""
    supplies = [entry for entry in entries if entry.kind == SUPPLY]
    credits_ = [entry for entry in entries if entry.kind == SUPPLY_CREDIT]
    purchases = [entry for entry in entries if entry.kind == PURCHASE]

    taxable_supplies = _sum(entry.gross for entry in supplies if entry.tax_code == 'standard')
    zero_rated = _sum(entry.gross for entry in supplies if entry.tax_code == 'zero_rated')
    exempt = _sum(entry.gross for entry in supplies if entry.tax_code == 'exempt')
    unclassified = _sum(entry.gross for entry in supplies if entry.tax_code == 'unclassified')
    credit_gross = _sum(entry.gross for entry in credits_)

    output_tax = _sum(entry.tax for entry in supplies) - _sum(entry.tax for entry in credits_)
    purchases_gross = _sum(entry.gross for entry in purchases)
    input_tax = _sum(entry.tax for entry in purchases)
    non_recoverable = _sum(entry.non_recoverable_tax for entry in purchases)

    # Debit and credit adjustments are the transition-adjustment slots task 117
    # change 5 asks for. They stay zero until a basis change produces one, and
    # they are named here so the return's arithmetic is complete either way.
    debit_adjustments = ZERO
    credit_adjustments = ZERO
    total_output = output_tax + debit_adjustments
    total_input = input_tax + credit_adjustments
    net = total_output - total_input

    return {
        'period_label': period.label,
        'period_start': period.start.isoformat(),
        'period_end': period.end.isoformat(),
        'clipped': period.clipped,
        'basis': period.basis,
        'filing_frequency': period.frequency,
        'gst_number': _gst_number(period),
        'registration': period.registration_id,
        'taxable_supplies_incl_tax': decimal_string(taxable_supplies, MONEY_PLACES),
        'zero_rated_supplies': decimal_string(zero_rated, MONEY_PLACES),
        'exempt_supplies': decimal_string(exempt, MONEY_PLACES),
        'unclassified_supplies': decimal_string(unclassified, MONEY_PLACES),
        'supply_credits_incl_tax': decimal_string(credit_gross, MONEY_PLACES),
        'output_tax': decimal_string(output_tax, MONEY_PLACES),
        'debit_adjustments': decimal_string(debit_adjustments, MONEY_PLACES),
        'total_output_tax': decimal_string(total_output, MONEY_PLACES),
        'purchases_incl_tax': decimal_string(purchases_gross, MONEY_PLACES),
        'input_tax': decimal_string(input_tax, MONEY_PLACES),
        'credit_adjustments': decimal_string(credit_adjustments, MONEY_PLACES),
        'total_input_tax': decimal_string(total_input, MONEY_PLACES),
        'non_recoverable_tax': decimal_string(non_recoverable, MONEY_PLACES),
        'input_tax_awaiting_payment': decimal_string(_sum(entry.tax for entry in awaiting), MONEY_PLACES),
        'net_gst': decimal_string(net, MONEY_PLACES),
        'net_gst_direction': _direction(net),
        'entry_count': len(entries),
        'currency_code': currency_code,
    }


def _direction(net):
    """Name the direction so no consumer has to parse a minus sign."""
    if net > 0:
        return 'payable'
    return 'refundable' if net < 0 else 'nil'


def _gst_number(period):
    """Return the number this period's return is filed under."""
    from tax.models import GstRegistration  # pylint: disable=import-outside-toplevel
    registration = GstRegistration.objects.filter(pk=period.registration_id).first()
    return registration.gst_number if registration else ''


def _period_totals(rows, transitions=()):
    """Sum the periods, per currency, withholding what cannot be consolidated."""
    summed = defaultdict(lambda: defaultdict(Decimal))
    money_fields = [
        column for column in PERIOD_COLUMNS
        if column not in {
            'period_label', 'period_start', 'period_end', 'clipped', 'basis',
            'filing_frequency', 'gst_number', 'registration',
            'net_gst_direction', 'entry_count', 'currency_code',
            # Filing status is a fact about the period, not a measure of it,
            # and drift is null wherever nothing was filed.
            'filed', 'filed_at', 'filed_total_drift',
        }
    ]
    for row in rows:
        bucket = summed[row['currency_code']]
        for field in money_fields:
            bucket[field] += Decimal(row[field])
        bucket['entry_count'] += row['entry_count']
    currencies = sorted(code for code in summed if code)
    per_currency = {
        code: {
            **{field: decimal_string(summed[code][field], MONEY_PLACES) for field in money_fields},
            'net_gst_direction': _direction(summed[code]['net_gst']),
            'entry_count': int(summed[code]['entry_count']),
        }
        for code in currencies
    }
    return {
        'periods': len(rows),
        'currencies': currencies,
        'by_currency': per_currency,
        'basis_transitions': [_transition_total(item) for item in transitions],
        # None means withheld, and must mean only that: consolidating two
        # currencies would need an exchange rate this application does not
        # hold. A range that simply saw no trading is nil, not unknown.
        'net_gst': _consolidated_net(currencies, per_currency),
    }


def _transition_total(transition):
    """Render one basis change's outstanding adjustment beside the periods.

    Change 5 asks for the transition work to be exposed rather than performed
    silently, so it travels with the report the operator files from.
    """
    return {
        'change_date': transition.change_date.isoformat(),
        'previous_basis': transition.previous_basis,
        'new_basis': transition.new_basis,
        'direction': transition.direction,
        'required': transition.required,
        'complete': transition.complete,
        'adjustment_tax': {
            code: decimal_string(value, MONEY_PLACES)
            for code, value in transition.adjustment_tax.items()
        },
    }


def _consolidated_net(currencies, per_currency):
    """Return the single-currency net, nil when nothing traded, None when mixed."""
    if not currencies:
        return decimal_string(ZERO, MONEY_PLACES)
    if len(currencies) > 1:
        return None
    return per_currency[currencies[0]]['net_gst']


def _entry_row(entry):
    """Render one derived entry as the drill-down row behind a period total."""
    return {
        'period_label': entry.period_label,
        'kind': entry.kind,
        'supply_date': entry.supply_date.isoformat(),
        'basis': entry.basis,
        'source_type': entry.source_type,
        'source_id': entry.source_id,
        'document_id': entry.document_id,
        'line_id': entry.line_id,
        'tax_code': entry.tax_code,
        'tax_rate': decimal_string(entry.tax_rate, MONEY_PLACES),
        'taxable': decimal_string(entry.taxable, MONEY_PLACES),
        'tax': decimal_string(entry.tax, MONEY_PLACES),
        'non_recoverable_tax': decimal_string(entry.non_recoverable_tax, MONEY_PLACES),
        'gross': decimal_string(entry.gross, MONEY_PLACES),
        'currency_code': entry.currency_code,
        'time_of_supply_source': entry.time_of_supply_source,
        'proxy': entry.proxy,
        'exclusion': entry.exclusion,
    }


def _entry_totals(entries):
    """Total the drill-down so it can be checked against the period it explains."""
    included = [entry for entry in entries if not entry.exclusion]
    return {
        'entries': len(entries),
        'included': len(included),
        'excluded': len(entries) - len(included),
        'taxable': decimal_string(_sum(entry.taxable for entry in included), MONEY_PLACES),
        'tax': decimal_string(_sum(entry.tax for entry in included), MONEY_PLACES),
        'gross': decimal_string(_sum(entry.gross for entry in included), MONEY_PLACES),
    }


def _data_quality(workspace, entries, rows, as_at):
    """Report every reason a figure is incomplete, with the rows behind it."""
    findings = []
    for exclusion, message in EXCLUSION_MESSAGES.items():
        matching = [entry for entry in entries if entry.exclusion == exclusion]
        if matching:
            findings.append(_finding(exclusion, len(matching), message, exclusion=exclusion))

    unclassified = [
        entry for entry in entries
        if not entry.exclusion and entry.tax_code == 'unclassified' and entry.kind == SUPPLY
    ]
    if unclassified:
        findings.append(_finding(
            'unclassified_tax_code', len(unclassified),
            'Some supplies at a zero rate have not been classified as '
            'zero-rated, exempt, or outside GST, so they are reported in none '
            'of those boxes.',
            tax_code='unclassified',
        ))

    non_recoverable = [entry for entry in entries if entry.non_recoverable_tax > 0]
    if non_recoverable:
        findings.append(_finding(
            'non_recoverable_input_tax', len(non_recoverable),
            'Tax on some purchases was not recoverable and is carried in the '
            'cost of the stock instead of being claimed.',
            kind=PURCHASE,
        ))

    purchases = [entry for entry in entries if entry.kind == PURCHASE]
    if purchases:
        findings.append(_finding(
            'receipt_level_tax_treatment', len(purchases),
            'Tax treatment is recorded for a whole receipt rather than per '
            'line, so a receipt mixing standard-rated and zero-rated purchases '
            'cannot be represented.',
            kind=PURCHASE,
        ))

    drifted = [
        row for row in rows
        if row.get('filed') and row.get('filed_total_drift') not in (None, '0.0000')
    ]
    if drifted:
        findings.append(_finding(
            'filed_total_drift', len(drifted),
            'A period already reported no longer derives the figure it was '
            'filed on. A later correction has changed it, and the difference '
            'belongs in a return rather than in a restatement.',
        ))

    outstanding = [item for item in basis_transitions(workspace) if item.required]
    if outstanding:
        findings.append(_finding(
            'basis_transition_incomplete', len(outstanding),
            'A change of accounting basis leaves a one-off adjustment for the '
            'debtors outstanding at the change date. It is reported in the '
            'totals and is not applied to any period automatically.',
        ))
        findings.append(_finding(
            'creditors_unavailable', len(outstanding),
            'The creditors side of a basis-change adjustment cannot be '
            'computed: no supplier payment date is recorded anywhere yet.',
        ))

    currencies = {row['currency_code'] for row in rows if row['currency_code']}
    if len(currencies) > 1:
        findings.append(_finding(
            'mixed_currency', len(currencies),
            'Supplies were made in more than one currency. Each is reported '
            'separately and the consolidated net GST is withheld, because no '
            'exchange rate is recorded.',
        ))

    findings.extend(
        _finding(warning['code'], 1, warning['message'])
        for warning in registration_warnings(workspace, as_at)
    )
    return findings


def _finding(code, count, message, **drill_down):
    """Build one data-quality entry with a link to the rows behind it."""
    query = '&'.join(f'{key}={value}' for key, value in sorted(drill_down.items()))
    return {
        'code': code,
        'count': count,
        'message': message,
        'drill_down': f'/reports/gst-entries/?{query}' if query else '/reports/gst-entries/',
    }


def _sum(values):
    """Total a series of money amounts, starting from an exact zero."""
    return sum(values, ZERO)


def _date_bounds(workspace, filters):
    """Return the inclusive range to report, defaulting to the open period.

    A GST report with no range asked for should answer the question the
    operator actually has, which is what the return they are about to file
    looks like. An unregistered workspace has no period, so it falls back to
    the current calendar month — the same default the commerce reports use.
    """
    start = filters.get('date_from')
    end = filters.get('date_to')
    if start and end:
        return _as_date(start), _as_date(end)
    today = local_date(workspace, _now())
    period = taxable_period_for(workspace, today)
    if period is not None:
        default_start, default_end = period.start, period.end
    else:
        default_start = date(today.year, today.month, 1)
        default_end = date(today.year, today.month, monthrange(today.year, today.month)[1])
    return (
        _as_date(start) if start else default_start,
        _as_date(end) if end else default_end,
    )


def _as_date(value):
    """Accept a filter value as either an ISO string or an already-parsed date."""
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(value).date()


def _now():
    """Return the current instant, isolated so a test can control the default."""
    from django.utils import timezone  # pylint: disable=import-outside-toplevel
    return timezone.now()
