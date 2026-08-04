"""Yield aggregation over recorded harvests.

Totals are grouped by one dimension at a time and reported per unit family.
Count, mass, and volume are never added together: a report that combined
forty fruit with twelve kilograms would be arithmetic without a meaning, so
each family gets its own total expressed in that family's reference unit.

"Season" is deliberately not a grouping. Which months make a season depends on
the hemisphere and on the crop, so guessing one would invent information the
records do not hold. A caller expresses a season as a `harvested_from` and
`harvested_to` range, and the month and year groupings supply the calendar
axis inside it.

Seed, plant, and harvest counts are likewise reported as separate integers.
Seeds sown divided by kilograms picked is not a yield ratio, and this module
never produces one.
"""

# pylint: disable=duplicate-code

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import models

from inventory.ledger import quantize_quantity
from inventory.units import convert_standard_quantity, get_unit_definition

from .batches import batch_seeds_sown, batch_specific_plants
from .lifecycle import LifecycleState, lifecycle_summaries
from .models import Harvest


class GroupBy(models.TextChoices):
    """The dimensions a yield report may be grouped by."""

    PLANT = 'plant', 'Plant'
    VARIETY = 'variety', 'Variety'
    BATCH = 'batch', 'Batch'
    GARDEN_SQUARE = 'garden_square', 'Garden square'
    GARDEN_ROW = 'garden_row', 'Garden row'
    MONTH = 'month', 'Month'
    YEAR = 'year', 'Year'


#: Groupings whose rows describe one crop, and so can carry the seed and plant
#: counts that lineage is defined for. A square, a row, or a calendar bucket may
#: hold several crops at once, so reporting a seed count against one would
#: attribute another crop's sowing to it. An individual plant has no lineage
#: worth reporting either: it came from one seed and is one plant, so the
#: counts would be trivia rather than information.
LINEAGE_GROUPINGS = {GroupBy.BATCH, GroupBy.VARIETY}

#: The label a row carries when its harvest recorded no growing location.
NO_LOCATION_LABEL = 'No location recorded'


def _reference_unit(unit_code):
    """Return the unit one family totals in, and the family's identity."""
    definition = get_unit_definition(unit_code)
    return definition.conversion_family, definition.reference_unit


def _family_total(harvests):
    """Total compatible harvests, keeping incompatible dimensions apart.

    Converting into a family's reference unit is exact: the target multiplier
    is always one, so the conversion is a multiplication and no division
    rounding can occur.
    """
    families = {}
    for harvest in harvests:
        family, reference = _reference_unit(harvest.unit_code)
        bucket = families.setdefault(family, {
            'conversion_family': family,
            'dimension': get_unit_definition(reference).dimension,
            'unit_code': reference,
            'total': Decimal('0'),
            'harvest_count': 0,
        })
        bucket['total'] += convert_standard_quantity(
            harvest.quantity,
            harvest.unit_code,
            reference,
        )
        bucket['harvest_count'] += 1
    return [
        {
            'conversion_family': bucket['conversion_family'],
            'dimension': bucket['dimension'],
            'unit_code': bucket['unit_code'],
            'quantity': f'{quantize_quantity(bucket["total"]):.9f}',
            'harvest_count': bucket['harvest_count'],
        }
        for _family, bucket in sorted(families.items())
    ]


def batch_harvest_totals(batch):
    """Return one batch's posted yield, one total per unit family."""
    return _family_total(
        Harvest.objects.filter(batch=batch, status=Harvest.Status.POSTED),
    )


def batch_harvest_finished_count(batch):
    """Return how many of this batch's plants were harvested out.

    Narrower than the batch's final-outcome count, which also counts plants
    that failed, were culled, were donated, or were retained.
    """
    summaries = lifecycle_summaries(
        batch_specific_plants(batch).order_by('pk').values_list('pk', flat=True),
    )
    return sum(
        1
        for summary in summaries.values()
        if summary.state == LifecycleState.HARVESTED
    )


def workspace_zone(workspace):
    """Return the timezone a workspace's calendar buckets are cut in."""
    return ZoneInfo(workspace.timezone)


def local_day_bounds(zone, harvested_from, harvested_to):
    """Return the half-open aware range two inclusive local dates describe."""
    start = None
    end = None
    if harvested_from is not None:
        start = datetime.combine(harvested_from, time.min, tzinfo=zone)
    if harvested_to is not None:
        end = datetime.combine(harvested_to + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _filtered_harvests(workspace, filters):
    """Return the posted harvests one report covers, in one query."""
    queryset = Harvest.objects.filter(
        workspace=workspace,
        status=Harvest.Status.POSTED,
    ).select_related(
        'batch__variety__plant',
        'garden_square',
        'garden_row',
    ).prefetch_related('plant_allocations')
    start, end = local_day_bounds(
        workspace_zone(workspace),
        filters.get('harvested_from'),
        filters.get('harvested_to'),
    )
    if start is not None:
        queryset = queryset.filter(harvested_at__gte=start)
    if end is not None:
        queryset = queryset.filter(harvested_at__lt=end)
    for field, lookup in (
        ('batch', 'batch_id'),
        ('variety', 'batch__variety_id'),
        ('garden_square', 'garden_square_id'),
        ('garden_row', 'garden_row_id'),
    ):
        value = filters.get(field)
        if value is not None:
            queryset = queryset.filter(**{lookup: value})
    return queryset


def _entity_key(group_by, harvest):
    """Return the identity and label of the group one harvest belongs to."""
    if group_by == GroupBy.BATCH:
        return harvest.batch_id, harvest.batch.code
    if group_by == GroupBy.VARIETY:
        variety = harvest.batch.variety
        return variety.pk, f'{variety.plant.name} — {variety.name}'
    if group_by == GroupBy.GARDEN_SQUARE:
        square = harvest.garden_square
        return (None, NO_LOCATION_LABEL) if square is None else (square.pk, str(square))
    row = harvest.garden_row
    return (None, NO_LOCATION_LABEL) if row is None else (row.pk, str(row))


def _period_key(group_by, harvest, zone):
    """Return the calendar bucket one harvest falls in, cut locally."""
    local = harvest.harvested_at.astimezone(zone)
    if group_by == GroupBy.YEAR:
        return f'{local.year:04d}', f'{local.year:04d}'
    return f'{local.year:04d}-{local.month:02d}', local.strftime('%B %Y')


def _grouped_harvests(harvests, group_by, zone):
    """Return each group's key, label, and harvests, in a stable order."""
    groups = {}
    for harvest in harvests:
        if group_by in (GroupBy.MONTH, GroupBy.YEAR):
            pairs = [_period_key(group_by, harvest, zone)]
        elif group_by == GroupBy.PLANT:
            pairs = [
                (allocation.plant_id, f'Plant {allocation.plant_id}')
                for allocation in harvest.plant_allocations.all()
            ]
        else:
            pairs = [_entity_key(group_by, harvest)]
        for key, label in pairs:
            groups.setdefault(key, {'label': label, 'harvests': []})
            groups[key]['harvests'].append(harvest)
    return sorted(
        groups.items(),
        key=lambda item: (item[0] is None, str(item[0])),
    )


def _plant_totals(harvests):
    """Split a plant's harvests into its own yield and its shared yield.

    A harvest attributed to three plants measured one crop, not three. Adding it
    to each plant would triple the yield, and dividing it would invent a split
    nobody observed, so shared harvests are reported beside the total rather
    than inside it.
    """
    own = [h for h in harvests if len(h.plant_allocations.all()) == 1]
    shared = [h for h in harvests if len(h.plant_allocations.all()) > 1]
    return _family_total(own), _family_total(shared)


def _lineage_counts(group_by, batches):
    """Return the seed, plant, and harvest counts for one group's crops.

    Reported as three independent integers. A seed count divided by a picked
    weight is not a rate of anything, so no ratio is derived from them here or
    anywhere downstream.
    """
    if group_by not in LINEAGE_GROUPINGS:
        return {
            'seeds_sown': None,
            'plants_observed': None,
            'plants_harvest_finished': None,
        }
    return {
        'seeds_sown': sum(batch_seeds_sown(batch) for batch in batches),
        'plants_observed': sum(
            batch_specific_plants(batch).count() for batch in batches
        ),
        'plants_harvest_finished': sum(
            batch_harvest_finished_count(batch) for batch in batches
        ),
    }


def harvest_report(workspace, filters):
    """Return one row per group describing what that group yielded.

    Aggregated in Python from a single query. Totalling in SQL would mean
    either summing incompatible units into one meaningless number or issuing a
    query per unit family, and the local calendar bucketing below has no
    portable SQL equivalent.
    """
    group_by = filters['group_by']
    zone = workspace_zone(workspace)
    harvests = list(_filtered_harvests(workspace, filters))
    rows = []
    for key, group in _grouped_harvests(harvests, group_by, zone):
        members = group['harvests']
        if group_by == GroupBy.PLANT:
            totals, shared_totals = _plant_totals(members)
        else:
            totals, shared_totals = _family_total(members), []
        batches = {harvest.batch_id: harvest.batch for harvest in members}
        rows.append({
            'group_by': group_by,
            'key': key,
            'label': group['label'],
            'harvest_count': len(members),
            'first_harvested_at': min(h.harvested_at for h in members),
            'last_harvested_at': max(h.harvested_at for h in members),
            'totals': totals,
            'shared_totals': shared_totals,
            **_lineage_counts(group_by, batches.values()),
        })
    return rows
