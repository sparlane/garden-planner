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
