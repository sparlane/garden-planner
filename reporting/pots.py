"""Pot-number to variety listing for pots too small to carry anything else."""

import re
from collections import OrderedDict

from plantings.models import SpecificPlantLocation

from .common import Report


COLUMNS = (
    'asset_code', 'plant_name', 'variety_id', 'variety_name', 'plant_count',
    'batch_codes', 'location_id', 'location_name', 'unit_id',
)


def _natural_key(asset_code):
    """Sort pot 2 before pot 10 while still ordering non-numeric codes."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', asset_code)
    ]


def pot_varieties(workspace, filters):
    """List which varieties stand in each numbered pot right now.

    One row per pot and variety, so a pot holding two varieties appears twice
    and a pot of three bulbs of one variety appears once with a count of three.
    Only open placements count: a plant that has been moved out of a pot no
    longer belongs on the pot's list.
    """
    placements = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=workspace,
        location_type=SpecificPlantLocation.CONTAINER_UNIT,
        ended__isnull=True,
        container_unit__active=True,
    ).select_related(
        'container_unit__current_location',
        'specific_plant__batch__variety__plant',
    )
    if filters.get('variety'):
        placements = placements.filter(
            specific_plant__batch__variety_id=filters['variety'],
        )
    if filters.get('plant'):
        placements = placements.filter(
            specific_plant__batch__variety__plant_id=filters['plant'],
        )
    if filters.get('location'):
        placements = placements.filter(
            container_unit__current_location_id=filters['location'],
        )

    grouped = OrderedDict()
    for placement in placements:
        unit = placement.container_unit
        batch = placement.specific_plant.batch
        variety = batch.variety
        row = grouped.setdefault((unit.pk, variety.pk), {
            'asset_code': unit.asset_code,
            'plant_name': variety.plant.name,
            'variety_id': variety.pk,
            'variety_name': variety.name,
            'plant_count': 0,
            'batch_codes': set(),
            'location_id': unit.current_location_id,
            'location_name': (
                unit.current_location.name if unit.current_location_id else None
            ),
            'unit_id': unit.pk,
        })
        row['plant_count'] += 1
        row['batch_codes'].add(batch.code)

    rows = []
    for row in grouped.values():
        row['batch_codes'] = ', '.join(sorted(row['batch_codes']))
        rows.append(row)
    if filters.get('order') == 'variety':
        rows.sort(key=lambda row: (
            row['plant_name'].lower(), row['variety_name'].lower(),
            _natural_key(row['asset_code']),
        ))
    else:
        rows.sort(key=lambda row: (
            _natural_key(row['asset_code']), row['variety_name'].lower(),
        ))
    return Report(
        name='pot-varieties', filters=filters, rows=rows, columns=COLUMNS,
        totals={
            'pots': len({row['unit_id'] for row in rows}),
            'plants': sum(row['plant_count'] for row in rows),
            'varieties': len({row['variety_id'] for row in rows}),
        },
        reconciliation={
            'identity_equation': 'rows = one per numbered pot and variety',
        },
    )
