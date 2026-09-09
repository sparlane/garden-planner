"""Media still owned by an unused pot fill, separate from container cost."""

from decimal import Decimal

from applications.models import InputApplication, InputApplicationLine
from inventory.ledger import quantize_quantity

from .generation_costs import quantize_cost


def pot_fill_contents(fill):
    """Total posted media per lot; a reversed application leaves nothing behind.

    Each pot-media line names exactly one whole fill. Application waste was
    never put into a pot, so only the applied quantity belongs to the fill.
    """
    rows = {}
    lines = InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED,
        targets__container_fill=fill,
    ).select_related('application', 'lot__item').order_by('pk')
    for line in lines:
        row = rows.setdefault(line.lot_id, {
            'lot': line.lot, 'base_unit': line.base_unit,
            'base_quantity': Decimal('0'), 'unit_cost': line.lot.base_unit_cost,
            'latest_application': line.application.applied_at,
        })
        row['base_quantity'] += line.applied_base_quantity
        row['latest_application'] = max(row['latest_application'], line.application.applied_at)
    return [{**row, 'base_quantity': quantize_quantity(row['base_quantity'])} for row in rows.values()]


def pot_fill_cost_breakdown(fill):
    """Report held, discarded and recovered media without inventing plant costs.

    Unknown acquisition costs remain unknown. The media ledger and these
    residuals are the source of the report; the pot's acquisition cost never
    changes when it is filled or cleaned.
    """
    media = pot_fill_contents(fill)
    unknown = any(row['unit_cost'] is None for row in media)
    applied = sum((row['base_quantity'] * (row['unit_cost'] or 0) for row in media), Decimal('0'))
    residuals = {'waste': Decimal('0'), 'reclaimed': Decimal('0')}
    for residual in fill.residuals.filter(kind='media'):
        unknown = unknown or residual.unit_cost is None
        residuals[residual.disposition] += residual.base_quantity * (residual.unit_cost or 0)
    amounts = {
        'applied_cost': applied,
        'held_cost': applied - sum(residuals.values()),
        'production_loss': residuals['waste'],
        'recovered_cost': residuals['reclaimed'],
    }
    return {
        'fill': fill.pk, 'container_count': fill.container_count,
        'currency_code': fill.workspace.currency_code, 'unknown_cost': unknown,
        **{key: None if unknown else quantize_cost(value) for key, value in amounts.items()},
    }
