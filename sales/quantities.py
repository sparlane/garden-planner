"""Quantity projections shared by reservations, dispatch, and returns."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Sum

from inventory.units import UnitCode


QUANTITY_QUANTUM = Decimal('0.000000001')


def measured(record):
    """Whether this commercial row allows partial measured dispatches."""
    return record.unit != UnitCode.EACH


def positive_quantity(value):
    """Validate precision before a service can move stock or round a column."""
    try:
        quantity = Decimal(str(value))
        if not quantity.is_finite() or quantity <= 0 or quantity >= Decimal('100000000000'):
            raise ValueError
        if quantity != quantity.quantize(QUANTITY_QUANTUM):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError({'quantity': 'Enter a positive quantity with at most nine decimal places.'}) from exc
    return quantity


def shipped_quantity(allocation):
    """Total effective dispatches against an immutable reservation."""
    return allocation.fulfillment_lines.filter(
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')


def remaining_quantity(allocation):
    """Reserved stock that has not yet left the source."""
    return allocation.promised_units - shipped_quantity(allocation)


def returned_quantity(line):
    """Effective physical returns, independent from monetary credits."""
    return line.return_lines.filter(
        sales_return__reversal_of__isnull=True,
        sales_return__reversal__isnull=True,
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
