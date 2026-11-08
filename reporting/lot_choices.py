"""A short list of stock lots to choose from, narrowed by plant and variety.

The reports that take a lot (traceability, balances) used to want its
database number typed in. This lists the lots by what they are, so a person
can pick one. A seed lot answers to the variety its catalog entry grows; a lot
of anything else (media, packaging) has no variety and appears only when no
plant or variety filter is set.
"""

from django.db.models import Q

from inventory.models import StockLot

from .common import Report

COLUMNS = (
    'lot_id', 'lot_identifier', 'item_name', 'plant_id', 'plant_name',
    'variety_id', 'variety_name', 'supplier_lot_reference', 'received_on',
    'expires_on',
)
SEARCH_FIELDS = (
    'identifier', 'supplier_lot_reference', 'item__name',
    'item__seed_catalog__plant_variety__name',
    'item__seed_catalog__plant_variety__plant__name',
)


def lot_choices(workspace, filters):
    """List lots, newest first, optionally for one plant, variety or search."""
    lots = StockLot.objects.filter(workspace=workspace).select_related(
        'item', 'item__seed_catalog__plant_variety__plant',
    ).order_by('-received_on', '-pk')
    if filters.get('plant'):
        lots = lots.filter(
            item__seed_catalog__plant_variety__plant_id=filters['plant'],
        )
    if filters.get('variety'):
        lots = lots.filter(
            item__seed_catalog__plant_variety_id=filters['variety'],
        )
    search = (filters.get('search') or '').strip()
    if search:
        match = Q()
        for field in SEARCH_FIELDS:
            match |= Q(**{f'{field}__icontains': search})
        lots = lots.filter(match)
    rows = []
    for lot in lots:
        catalog = getattr(lot.item, 'seed_catalog', None)
        variety = catalog.plant_variety if catalog else None
        rows.append({
            'lot_id': lot.pk,
            'lot_identifier': lot.identifier,
            'item_name': lot.item.name,
            'plant_id': variety.plant_id if variety else None,
            'plant_name': variety.plant.name if variety else None,
            'variety_id': variety.pk if variety else None,
            'variety_name': variety.name if variety else None,
            'supplier_lot_reference': lot.supplier_lot_reference,
            'received_on': lot.received_on.isoformat() if lot.received_on else None,
            'expires_on': lot.expires_on.isoformat() if lot.expires_on else None,
        })
    return Report(
        name='lot-choices', filters=filters, rows=rows, columns=COLUMNS,
        totals={'lots': len(rows)},
    )
