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
    values[-1] = money(values[-1] + total - sum(values))
    return values


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


def proportional_refund(amount, available_lines):  # pylint: disable=too-many-locals
    """Allocate an inclusive refund and preserve each source line's ratios."""
    amount = money(amount)
    ordered = sorted(available_lines, key=lambda row: row['line'].pk)
    available_total = money(sum(row['remaining_total'] for row in ordered))
    if amount <= 0 or amount > available_total:
        raise ValueError('Refund amount exceeds the selected refundable value.')
    inclusive = []
    remaining = amount
    for index, row in enumerate(ordered):
        if index == len(ordered) - 1:
            share = remaining
        else:
            share = money(amount * row['remaining_total'] / available_total)
            share = min(share, row['remaining_total'], remaining)
        inclusive.append(share)
        remaining = money(remaining - share)
    if remaining:
        inclusive[-1] = money(inclusive[-1] + remaining)
    result = []
    for row, total in zip(ordered, inclusive):
        source = row['line']
        fraction = total / source.total_incl_tax
        subtotal = money(source.subtotal_ex_tax * fraction)
        tax = money(total - subtotal)
        gross = money(source.gross_ex_tax * fraction)
        discount = money(gross - subtotal)
        result.append({
            'line': source,
            'gross_ex_tax': gross,
            'discount_ex_tax': discount,
            'subtotal_ex_tax': subtotal,
            'tax_total': tax,
            'total_incl_tax': total,
        })
    return result
