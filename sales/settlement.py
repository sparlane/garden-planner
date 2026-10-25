"""Settle a cost of sale that was provisional when the line was posted (task 139).

A plant sold before its batch's output is finalized is dispatched at a figure
that can still move, and the line says so with `cogs_provisional`. Finalizing
the batch is the event that makes the figure final, because it freezes every
plant's share for good. This module writes that figure beside the line as a
`FulfillmentLineSettlement` and answers, for the reports, what a line cost once
any settlement is taken into account.

Only a line that sold one identified plant is settled. A block of anonymous
stock keeps being re-divided after the batch is finalized, so finalization is
not the event that settles it and its lines stay provisional. A pot sold with
passengers is a sum of parts and is left alone too; settling it would need the
pot's own half re-read as well.
"""

from decimal import Decimal

from django.db import transaction

from costing.services import is_frozen

from .calculations import money
from .cost_of_sale import CostOfSale, plant_cost_of_sale
from .models import FulfillmentLine, FulfillmentLineSettlement


def settlement_of(line):
    """Return the settlement that stands for this line, if any.

    Reads `line.settlements.all()` so a queryset that prefetched them costs no
    query per line.
    """
    rows = list(line.settlements.all())
    return rows[-1] if rows else None


def cost_of_line(line):
    """Return what this dispatched line cost, taking its settlement into account.

    A settlement stands only while the plant's batch is still finalized. A batch
    reopened after the sale puts the plant's share back in play, and the
    snapshot taken at dispatch is then the honest reading again.
    """
    settlement = settlement_of(line)
    plant = line.allocation.plant if line.allocation.plant_id else None
    if settlement is not None and plant is not None and is_frozen(plant.batch):
        return CostOfSale(settlement.cogs_amount, settlement.cogs_currency_code, False)
    return CostOfSale(line.cogs_amount, line.cogs_currency_code, line.cogs_provisional)


def restored_cost(return_line, line_cost):
    """Return the cost of sale a return gives back, following any settlement.

    A line settled as unstateable gives back nothing stateable, whatever figure
    its return recorded while the cost was still provisional.
    """
    line = return_line.fulfillment_line
    if line_cost.provisional or settlement_of(line) is None:
        return return_line.cogs_amount
    if line_cost.amount is None:
        return None
    if return_line.quantity == line.quantity:
        return line_cost.amount
    return money(line_cost.amount * return_line.quantity / line.quantity)


def provisional_plant_lines(batch):
    """Return the live, plant-backed lines of a batch still marked provisional."""
    return FulfillmentLine.objects.filter(
        allocation__plant__batch=batch,
        cogs_provisional=True,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related('allocation__plant__batch').prefetch_related('settlements')


@transaction.atomic
def settle_batch_sales(batch, user, source, reason=''):
    """Settle every provisional plant line of a finalized batch; return the new rows.

    Idempotent: a line whose standing settlement already states the figure the
    plant now carries is left alone. A batch that is not finalized settles
    nothing, because its figure can still move.
    """
    if not is_frozen(batch):
        return []
    settled = []
    for line in provisional_plant_lines(batch):
        cost = plant_cost_of_sale(line.allocation.plant)
        if cost.provisional:
            continue
        standing = settlement_of(line)
        if standing is not None and (
            standing.cogs_amount, standing.cogs_currency_code,
        ) == (cost.amount, cost.currency_code):
            continue
        settled.append(FulfillmentLineSettlement.objects.create(
            line=line,
            cogs_amount=None if cost.amount is None else Decimal(cost.amount),
            cogs_currency_code=cost.currency_code,
            source=source, reason=reason, created_by=user,
        ))
    return settled
