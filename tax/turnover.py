"""Watch taxable turnover against the thresholds that force a change.

Registration is compulsory once taxable turnover in any twelve-month period
reaches NZ$60,000, and the accounting basis and filing frequency a workspace
may use are capped by turnover as well. None of that is enforced here: a
nursery that has outgrown the payments basis does not stop being on it the
moment it crosses the line, and refusing to record what is actually true would
leave its returns unproducible. Every finding is a warning.

Turnover is derived from the commerce records directly rather than from any
GST record, because the case this exists for is the workspace that is not
registered yet and therefore has no arrangement, no basis, and no periods.
Supplies are measured as made — the invoice basis — since that is what the
threshold test asks about regardless of how the workspace accounts.
"""

from datetime import date
from decimal import Decimal

from inventory.ledger import quantize_money

from .facts import workspace_order_facts
from .periods import local_date, registration_history
from .recognition import INVOICE, TURNOVER_CODES, order_recognition


#: Compulsory registration once taxable turnover reaches this in any
#: twelve-month period, past or expected.
REGISTRATION_THRESHOLD = Decimal('60000')
#: The payments basis is available only below this turnover.
PAYMENTS_BASIS_CEILING = Decimal('2000000')
#: Six-monthly filing is available only below this turnover.
SIX_MONTHLY_CEILING = Decimal('500000')
#: Above this, monthly filing is compulsory.
MONTHLY_COMPULSORY_FLOOR = Decimal('24000000')

#: How much recent trading the forward projection is annualised from.
PROJECTION_MONTHS = 3
#: The six-monthly cycles Inland Revenue offers, as the month a period ends in.
SIX_MONTHLY_ANCHORS = (3, 4, 5, 9, 10, 11)

ZERO = Decimal('0.0000')


def months_before(day, months):
    """Return the same day-of-month some months earlier, clamped for February.

    A plain 365-day subtraction gets the leap-year boundary wrong, which for a
    rolling twelve-month test means a supply drops out of the window a day
    early or late — exactly at the point the answer changes.
    """
    index = day.year * 12 + day.month - 1 - months
    year, month = index // 12, index % 12 + 1
    for candidate in range(day.day, 0, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    raise ValueError('No valid date in the target month.')


def taxable_turnover(workspace, start, end):
    """Return the value of taxable supplies made in a range, per currency.

    Standard-rated and zero-rated supplies count; exempt and out-of-scope
    supplies do not. An unclassified supply is neither counted nor ignored —
    it is reported separately, because assuming either way would move the
    answer across a threshold nobody decided to cross.
    """
    counted = {}
    unclassified = {}
    for facts in workspace_order_facts(workspace, start, end):
        recognition = order_recognition(facts, INVOICE)
        for event in recognition.events:
            if not start <= event.supply_date <= end:
                continue
            bucket = counted if _counts_towards_turnover(event) else None
            if bucket is None and _is_unclassified(event):
                bucket = unclassified
            if bucket is None:
                continue
            sign = -1 if event.kind != 'supply' else 1
            current = bucket.get(facts.currency_code, ZERO)
            bucket[facts.currency_code] = current + sign * event.taxable
    return {
        'start': start,
        'end': end,
        'taxable': {code: quantize_money(value) for code, value in counted.items()},
        'unclassified': {code: quantize_money(value) for code, value in unclassified.items()},
    }


def _counts_towards_turnover(event):
    """Whether one event's value belongs in the threshold measurement."""
    return event.tax_code in TURNOVER_CODES or event.kind != 'supply'


def _is_unclassified(event):
    """Whether an event's value cannot yet be said to count or not."""
    return event.tax_code == 'unclassified'


def rolling_turnover(workspace, on_date):
    """Return taxable turnover over the twelve months ending on a date."""
    return taxable_turnover(workspace, months_before(on_date, 12), on_date)


def turnover_projection(workspace, on_date):
    """Annualise recent trading, labelled so it cannot pass for a measurement.

    The legal test is whether turnover is *expected* to exceed the threshold,
    and no system can know an expectation. Annualising the last quarter is a
    prompt to think about it, not an answer, so the method travels with the
    number.
    """
    recent = taxable_turnover(workspace, months_before(on_date, PROJECTION_MONTHS), on_date)
    factor = Decimal(12) / Decimal(PROJECTION_MONTHS)
    return {
        'method': f'last_{PROJECTION_MONTHS}_months_annualised',
        'start': recent['start'],
        'end': recent['end'],
        'taxable': {
            code: quantize_money(value * factor)
            for code, value in recent['taxable'].items()
        },
    }


def registration_warnings(workspace, on_date=None, registration=None):
    """Return every threshold or eligibility finding, worst case first.

    Nothing here refuses anything. The findings reach the settings screen, the
    period report, and the response to recording an arrangement, so the
    consequence of a choice is visible at the moment it is made.
    """
    on_date = on_date or local_date(workspace, _now())
    if registration is None:
        registration = _registration_in_force(workspace, on_date)
    rolling = rolling_turnover(workspace, on_date)
    highest = max(rolling['taxable'].values(), default=ZERO)
    warnings = []
    if registration is None:
        warnings.extend(_threshold_warnings(workspace, on_date, highest))
    else:
        warnings.extend(_eligibility_warnings(registration, highest))
    warnings.extend(_configuration_warnings(workspace, rolling))
    return warnings


def _threshold_warnings(workspace, on_date, highest):
    """Findings that apply only while the workspace is not registered."""
    warnings = []
    if highest >= REGISTRATION_THRESHOLD:
        warnings.append(_warning(
            'threshold_exceeded',
            'Taxable turnover over the last twelve months has reached the '
            'NZ$60,000 registration threshold. Registration is compulsory.',
            highest,
        ))
    else:
        projected = max(turnover_projection(workspace, on_date)['taxable'].values(), default=ZERO)
        if projected >= REGISTRATION_THRESHOLD:
            warnings.append(_warning(
                'threshold_projected',
                'Recent trading annualises above the NZ$60,000 threshold. '
                'Registration is compulsory if you expect to exceed it.',
                projected,
            ))
    return warnings


def _eligibility_warnings(registration, highest):
    """Findings about a basis or frequency turnover has outgrown."""
    warnings = []
    if registration.basis == 'payments' and highest > PAYMENTS_BASIS_CEILING:
        warnings.append(_warning(
            'payments_basis_ineligible',
            'Turnover is above NZ$2,000,000, which is the ceiling for the '
            'payments basis.',
            highest,
            PAYMENTS_BASIS_CEILING,
        ))
    if registration.filing_frequency == 'six_monthly' and highest > SIX_MONTHLY_CEILING:
        warnings.append(_warning(
            'six_monthly_ineligible',
            'Turnover is above NZ$500,000, which is the ceiling for '
            'six-monthly filing.',
            highest,
            SIX_MONTHLY_CEILING,
        ))
    if registration.filing_frequency != 'monthly' and highest > MONTHLY_COMPULSORY_FLOOR:
        warnings.append(_warning(
            'monthly_filing_required',
            'Turnover is above NZ$24,000,000, so monthly filing is compulsory.',
            highest,
            MONTHLY_COMPULSORY_FLOOR,
        ))
    if registration.filing_frequency == 'six_monthly' and registration.period_anchor_month not in SIX_MONTHLY_ANCHORS:
        warnings.append(_warning(
            'six_monthly_cycle_unusual',
            'Six-monthly periods normally end in March/September, '
            'April/October, or May/November.',
        ))
    return warnings


def _configuration_warnings(workspace, rolling):
    """Findings about a workspace whose settings do not describe a NZ entity."""
    warnings = []
    if workspace.currency_code != 'NZD':
        warnings.append(_warning(
            'workspace_currency_not_nzd',
            f'The workspace currency is {workspace.currency_code}. A GST '
            'return is filed in New Zealand dollars.',
        ))
    if len(rolling['taxable']) > 1:
        warnings.append(_warning(
            'mixed_currency',
            'Supplies were made in more than one currency, so turnover cannot '
            'be totalled without an exchange rate.',
        ))
    if any(value > 0 for value in rolling['unclassified'].values()):
        warnings.append(_warning(
            'unclassified_turnover',
            'Some supplies at a zero rate have not been classified as '
            'zero-rated, exempt, or outside GST, so they are counted neither '
            'towards nor against the threshold.',
            max(rolling['unclassified'].values()),
        ))
    return warnings


def _warning(code, message, value=None, threshold=None):
    """Build one finding in the shape every surface renders."""
    return {
        'code': code,
        'message': message,
        'value': None if value is None else str(quantize_money(value)),
        'threshold': None if threshold is None else str(quantize_money(threshold)),
    }


def _registration_in_force(workspace, on_date):
    """Return the arrangement applying on a date, reusing one history read."""
    from .periods import registration_in_force  # pylint: disable=import-outside-toplevel
    return registration_in_force(workspace, on_date, history=registration_history(workspace))


def _now():
    """Return the current instant, isolated so a test can control the date."""
    from django.utils import timezone  # pylint: disable=import-outside-toplevel
    return timezone.now()
