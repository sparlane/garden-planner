"""Deterministic commercial arithmetic for sales-order snapshots."""

from decimal import Decimal, ROUND_HALF_UP
from typing import NamedTuple

from django.db.models import Sum


MONEY_QUANTUM = Decimal('0.0001')
PERCENT_DIVISOR = Decimal('100')


def money(value):
    """Round a money value using the ledger's established convention."""
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


class LineAmounts(NamedTuple):
    """Canonical values calculated from one line's entered terms."""

    gross_ex_tax: Decimal
    discount_ex_tax: Decimal
    subtotal_ex_tax: Decimal
    tax_total: Decimal
    total_incl_tax: Decimal


def _entered_discount(quantity, unit_price, discount_type, discount_value):
    gross = money(Decimal(quantity) * Decimal(unit_price))
    if discount_type == 'fixed':
        discount = money(discount_value)
    elif discount_type == 'percentage':
        discount = money(gross * Decimal(discount_value) / PERCENT_DIVISOR)
    else:
        discount = Decimal('0.0000')
    return gross, discount


def calculate_line(line):
    """Calculate a line while preserving its entered inclusive/exclusive mode."""
    entered_gross, entered_discount = _entered_discount(
        line.quantity,
        line.unit_price,
        line.discount_type,
        line.discount_value,
    )
    if line.order.prices_include_tax:
        total = money(entered_gross - entered_discount)
        divisor = Decimal('1') + Decimal(line.tax_rate) / PERCENT_DIVISOR
        subtotal = money(total / divisor)
        tax = money(total - subtotal)
        gross_ex_tax = money(entered_gross / divisor)
        discount_ex_tax = money(gross_ex_tax - subtotal)
    else:
        gross_ex_tax = entered_gross
        discount_ex_tax = entered_discount
        subtotal = money(gross_ex_tax - discount_ex_tax)
        tax = money(subtotal * Decimal(line.tax_rate) / PERCENT_DIVISOR)
        total = money(subtotal + tax)
    return LineAmounts(gross_ex_tax, discount_ex_tax, subtotal, tax, total)


def refresh_order_totals(order):
    """Store totals from the order's canonical line snapshots."""
    totals = order.lines.aggregate(
        gross=Sum('gross_ex_tax'),
        discount=Sum('discount_ex_tax'),
        subtotal=Sum('subtotal_ex_tax'),
        tax=Sum('tax_total'),
        total=Sum('total_incl_tax'),
    )
    values = {
        'gross_ex_tax': money(totals['gross'] or 0),
        'discount_total_ex_tax': money(totals['discount'] or 0),
        'subtotal_ex_tax': money(totals['subtotal'] or 0),
        'tax_total': money(totals['tax'] or 0),
        'total_incl_tax': money(totals['total'] or 0),
    }
    type(order).objects.filter(pk=order.pk).update(**values)
    for field, value in values.items():
        setattr(order, field, value)
    return order


def distribute_money(total, count):
    """Split a snapshotted amount exactly across stable one-based positions."""
    total = money(total)
    if count < 1:
        raise ValueError('A money distribution needs at least one position.')
    share = money(total / count)
    values = [share] * count
    # The last position absorbs the rounding residual, which is what makes the
    # split exact. Nothing clamps the positions, so this cannot overdraw one.
    values[-1] = money(values[-1] + total - sum(values))
    assert_parts_reconcile(values, total, 'money distribution')
    return values


def assert_parts_reconcile(parts, total, description):
    """Fail loudly when an exact split stops adding back to its source."""
    allocated = money(sum(parts))
    if allocated != total:
        raise RuntimeError(
            f'The {description} allocated {allocated:f} against {total:f}.'
        )


def line_position_amounts(line):
    """Return the canonical commercial amounts for every exact item position."""
    fields = (
        'gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax',
        'tax_total', 'total_incl_tax',
    )
    distributed = {
        field: distribute_money(getattr(line, field), line.quantity)
        for field in fields
    }
    return {
        position: {
            field: distributed[field][position - 1]
            for field in fields
        }
        for position in range(1, line.quantity + 1)
    }


def proportional_refund(amount, available_lines):
    """Allocate an inclusive refund and preserve each source line's ratios."""
    amount = money(amount)
    ordered = sorted(available_lines, key=lambda row: row['line'].pk)
    available_total = money(sum(row['remaining_total'] for row in ordered))
    if amount <= 0 or amount > available_total:
        raise ValueError('Refund amount exceeds the selected refundable value.')
    inclusive = _allocate_inclusive(amount, ordered, available_total)
    return [
        _refund_components(row['line'], total)
        for row, total in zip(ordered, inclusive)
    ]


def _allocate_inclusive(amount, ordered, available_total):
    """Split an inclusive refund proportionally without overdrawing a line.

    Each share is capped at its own line's remaining total before anything
    else, so a rounding residual can never be dumped on a line with no room
    for it. That case is real rather than theoretical: 344.1157 spread over
    four ordinary lines and a 0.0040 residue left by an earlier partial refund
    rounds down often enough that the residue would be handed 0.0041, driving
    its remaining refundable value negative for every later refund.

    The residual those caps leave is then handed back from the last line
    forwards to whichever lines still have headroom, which is what makes the
    parts add back to the requested amount exactly.
    """
    shares = [
        min(
            money(amount * row['remaining_total'] / available_total),
            row['remaining_total'],
        )
        for row in ordered
    ]
    residual = money(amount - sum(shares))
    for index in reversed(range(len(shares))):
        if not residual:
            break
        headroom = money(ordered[index]['remaining_total'] - shares[index])
        step = max(min(residual, headroom), -shares[index])
        shares[index] = money(shares[index] + step)
        residual = money(residual - step)
    assert_parts_reconcile(shares, amount, 'refund allocation')
    return shares


def _refund_components(source, total):
    """Rebuild one refunded share from its source line's recognized ratios."""
    if not source.total_incl_tax:
        # A line given away at full discount has no ratios to preserve, and
        # its remaining refundable value is zero, so its share is zero too.
        return {
            'line': source,
            'gross_ex_tax': money(0),
            'discount_ex_tax': money(0),
            'subtotal_ex_tax': money(0),
            'tax_total': money(0),
            'total_incl_tax': total,
        }
    fraction = total / source.total_incl_tax
    subtotal = money(source.subtotal_ex_tax * fraction)
    gross = money(source.gross_ex_tax * fraction)
    return {
        'line': source,
        'gross_ex_tax': gross,
        'discount_ex_tax': money(gross - subtotal),
        'subtotal_ex_tax': subtotal,
        'tax_total': money(total - subtotal),
        'total_incl_tax': total,
    }
