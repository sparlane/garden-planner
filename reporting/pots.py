"""Pot-number to variety listing for pots too small to carry anything else."""

import re
from collections import defaultdict

from plantings.models import SpecificPlantLocation

from .common import Report


COLUMNS = (
    'pots', 'pot_from', 'pot_to', 'plant_name', 'variety_id', 'variety_name',
    'pot_count', 'plant_count', 'batch_codes',
)


def _natural_key(asset_code):
    """Sort pot 2 before pot 10 while still ordering non-numeric codes."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', asset_code)
    ]


def _open_pots(workspace):
    """Map each numbered pot holding plants to its varieties and plant counts."""
    placements = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=workspace,
        location_type=SpecificPlantLocation.CONTAINER_UNIT,
        ended__isnull=True,
        container_unit__active=True,
    ).select_related(
        'container_unit', 'specific_plant__batch__variety__plant',
    )
    pots = {}
    for placement in placements:
        unit = placement.container_unit
        batch = placement.specific_plant.batch
        pot = pots.setdefault(unit.pk, {
            'code': unit.asset_code,
            'number': int(unit.asset_code) if unit.asset_code.isdigit() else None,
            'location_id': unit.current_location_id,
            'varieties': {},
        })
        entry = pot['varieties'].setdefault(
            batch.variety_id, {'variety': batch.variety, 'plants': 0, 'batches': set()},
        )
        entry['plants'] += 1
        entry['batches'].add(batch.code)
    return pots


def _runs(pots):
    """Group each variety's pots into runs of consecutive occupied pots.

    A number with no plants in it (sold, empty, never numbered) is not in
    `pots` at all, so it never breaks a run; only a pot holding something else
    between two pots of the variety does. Pots whose code is not a number
    cannot be ranged, so each stands alone.
    """
    numbered = sorted(pot['number'] for pot in pots.values() if pot['number'] is not None)
    position = {number: index for index, number in enumerate(numbered)}
    by_variety = defaultdict(list)
    for pot in pots.values():
        for variety_id in pot['varieties']:
            by_variety[variety_id].append(pot)
    for variety_id, members in by_variety.items():
        members.sort(key=lambda pot: (pot['number'] is None, pot['number'] or 0, _natural_key(pot['code'])))
        run = []
        for pot in members:
            joins = run and pot['number'] is not None and run[-1]['number'] is not None and (
                position[pot['number']] - position[run[-1]['number']] == 1
            )
            if run and not joins:
                yield variety_id, run
                run = []
            run.append(pot)
        if run:
            yield variety_id, run


def pot_varieties(workspace, filters):
    """List which varieties stand in which numbered pots right now.

    Consecutive pots of one variety collapse to a range such as `1-4`. Only a
    pot holding a different variety splits a range; pots with nothing in them
    are skipped over. Range and location filters narrow which pots are
    reported but not how runs form, so asking for one variety or one stretch
    never merges pots that a neighbour of another variety would have split.
    """
    low, high = filters.get('pot_from'), filters.get('pot_to')

    def in_scope(pot):
        if (low is not None or high is not None) and pot['number'] is None:
            return False
        if low is not None and pot['number'] < low:
            return False
        if high is not None and pot['number'] > high:
            return False
        if filters.get('location') and pot['location_id'] != filters['location']:
            return False
        return True

    rows = []
    for variety_id, run in _runs(_open_pots(workspace)):
        members = [pot for pot in run if in_scope(pot)]
        if not members:
            continue
        entries = [pot['varieties'][variety_id] for pot in members]
        variety = entries[0]['variety']
        if filters.get('variety') and variety.pk != filters['variety']:
            continue
        if filters.get('plant') and variety.plant_id != filters['plant']:
            continue
        first, last = members[0], members[-1]
        rows.append({
            'pots': first['code'] if first is last else f"{first['code']}-{last['code']}",
            'pot_from': first['number'],
            'pot_to': last['number'],
            'plant_name': variety.plant.name,
            'variety_id': variety.pk,
            'variety_name': variety.name,
            'pot_count': len(members),
            'plant_count': sum(entry['plants'] for entry in entries),
            'batch_codes': ', '.join(sorted(set().union(*(e['batches'] for e in entries)))),
            '_sort': _natural_key(first['code']),
        })
    if filters.get('order') == 'variety':
        rows.sort(key=lambda row: (
            row['plant_name'].lower(), row['variety_name'].lower(), row['_sort'],
        ))
    else:
        rows.sort(key=lambda row: (row['_sort'], row['variety_name'].lower()))
    for row in rows:
        del row['_sort']
    return Report(
        name='pot-varieties', filters=filters, rows=rows, columns=COLUMNS,
        totals={
            'pots': sum(row['pot_count'] for row in rows),
            'plants': sum(row['plant_count'] for row in rows),
            'varieties': len({row['variety_id'] for row in rows}),
        },
        reconciliation={
            'identity_equation': (
                'rows = one per run of consecutive pots holding a variety'
            ),
        },
    )
