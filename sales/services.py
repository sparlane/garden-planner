"""Transactional commands for sales orders and exact reservations."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import SalesOrder, SalesOrderNumberSequence


@transaction.atomic
def create_order(workspace, user, **values):
    """Create an order with locked numbering and workspace term snapshots."""
    sequence, _created = SalesOrderNumberSequence.objects.select_for_update().get_or_create(
        workspace=workspace,
    )
    number = sequence.next_number
    sequence.next_number += 1
    sequence.save(update_fields=['next_number'])
    status = values.pop('status', SalesOrder.Status.DRAFT)
    today = timezone.localdate()
    if status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
        raise ValidationError({'status': 'Orders must begin as a quote or draft.'})
    defaults = {
        'currency_code': workspace.currency_code,
        'prices_include_tax': workspace.sales_prices_include_tax,
        'created_by': user,
        'quote_date': today if status == SalesOrder.Status.QUOTE else None,
        'order_date': today if status == SalesOrder.Status.DRAFT else None,
    }
    defaults.update(values)
    return SalesOrder.objects.create(
        workspace=workspace,
        order_number=f'SO-{number:06d}',
        status=status,
        **defaults,
    )


@transaction.atomic
def update_pricing_mode(order, prices_include_tax):
    """Reinterpret entered draft terms in a newly selected pricing mode."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in {SalesOrder.Status.QUOTE, SalesOrder.Status.DRAFT}:
        raise ValidationError({'status': 'Confirmed commercial terms are immutable.'})
    order.prices_include_tax = prices_include_tax
    order.save(update_fields=['prices_include_tax', 'updated'])
    for line in order.lines.select_related('order').order_by('pk'):
        line.order = order
        line.save()
    order.refresh_from_db()
    return order
