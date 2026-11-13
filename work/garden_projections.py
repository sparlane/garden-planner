"""Garden milestones read recorded crop dates without inventing a schedule.

Offsets belong to the gardener's rule. Missing cultivation ranges and dates
recorded only as first observations cannot become predicted sowing milestones.
"""

from datetime import timedelta

from plantings.garden_register import garden_register_projection
from plantings.lifecycle import PRESENT_STATES
from plantings.models import (
    GardenPlanting, GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting,
    GardenSquareTransplant, SpecificPlant,
)
from plants.metadata import variety_days
from plants.models import MaturityBasis, PlantVariety

from .models import WorkTaskRule


TARGET_MODELS = {
    'aggregate': GardenPlanting,
    'individual': SpecificPlant,
    'direct-row': GardenRowDirectSowPlanting,
    'direct-square': GardenSquareDirectSowPlanting,
    'transplant': GardenSquareTransplant,
}


def _known_planting(target):
    origin = target if isinstance(target, GardenPlanting) else getattr(target, 'garden_planting', None)
    return origin is None or origin.date_basis == GardenPlanting.DateBasis.PLANTED


# Each anchor has distinct eligibility; early returns keep unknown dates out.
# pylint: disable=too-many-return-statements,too-many-branches
def _dates(rule, row, target, variety, calendar_day):
    """Return a source window, or nothing when its anchor is not known."""
    if rule.trigger == WorkTaskRule.Trigger.GARDEN_FINISHED:
        return (row['finished_on'], row['finished_on']) if row['finished_on'] and row['state'] not in PRESENT_STATES else None
    if row['finished_on'] or row['state'] not in {'current', *PRESENT_STATES}:
        return None
    if calendar_day is not None:
        return (calendar_day, calendar_day) if row['quantity'] != 0 else None
    if row['quantity'] == 0 or not _known_planting(target):
        return None
    planted = row['planted_on']
    if rule.trigger == WorkTaskRule.Trigger.GARDEN_PLANTED:
        return (planted, planted) if planted else None
    if rule.trigger == WorkTaskRule.Trigger.MATURITY:
        # Existing legacy and transplant projectors keep their occurrence keys,
        # including the acknowledgements made before Garden reminders shipped.
        if row['target_kind'] != 'aggregate':
            if not (isinstance(target, SpecificPlant) and target.garden_planting_id and variety.effective_maturity_basis == MaturityBasis.SEED):
                return None
        if row['source'] == GardenPlanting.Source.DIRECT_SEED:
            minimum, maximum = variety_days(variety, 'maturity')
            if planted and minimum is not None and maximum is not None:
                return planted + timedelta(days=minimum), planted + timedelta(days=maximum)
            return None
        return (row['expected_harvest_early'], row['expected_harvest_late']) if row['expected_harvest_early'] and row['expected_harvest_late'] else None
    # Individual plants have already emerged, and bought plants are not sowings.
    if row['record_type'] == 'individual' or row['source'] not in {
            GardenPlanting.Source.DIRECT_SEED, GardenPlanting.Source.INDOOR_RAISED_SEED}:
        return None
    if row['target_kind'] == 'transplant':
        return None
    minimum, maximum = variety_days(variety, 'germination')
    if planted is None or minimum is None or maximum is None:
        return None
    if rule.trigger == WorkTaskRule.Trigger.GERMINATION_WINDOW_END:
        minimum = maximum
    return planted + timedelta(days=minimum), planted + timedelta(days=maximum)


def _source_data(workspace):
    """Load one register and its authoritative targets once per queue request."""
    rows = garden_register_projection(workspace)
    targets = {
        kind: model.objects.filter(workspace=workspace).select_related(
            *(['garden_planting'] if kind == 'individual' else []),
        ).in_bulk([row['record_id'] for row in rows if row['target_kind'] == kind])
        for kind, model in TARGET_MODELS.items()
    }
    varieties = PlantVariety.objects.filter(workspace=workspace).select_related('plant').in_bulk()
    return rows, targets, varieties


def garden_date_tasks(rule, make_task, link_type, calendar_day=None, cache=None):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Project one stable occurrence per register identity and rule."""
    cache = {} if cache is None else cache
    if rule.workspace_id not in cache:
        cache[rule.workspace_id] = _source_data(rule.workspace)
    rows, targets, varieties = cache[rule.workspace_id]
    tasks = []
    for row in rows:
        if rule.variety_id and row['variety'] != rule.variety_id:
            continue
        # Register locations include garden geometry, which is deliberately not
        # coerced into the household Location catalog's primary keys.
        if rule.location_id and row['location'] != f'location:{rule.location_id}':
            continue
        if rule.stage_id:
            continue
        target = targets[row['target_kind']][row['record_id']]
        window = _dates(rule, row, target, varieties[row['variety']], calendar_day)
        if window is None:
            continue
        label = f"{row['plant_name']} — {row['variety_name']} — {row['location_label']}"
        occurrence = f"garden:{row['key']}"
        if calendar_day is not None:
            occurrence += f':{calendar_day.isoformat()}'
        task = make_task(
            rule, occurrence, f'{rule.name}: {label}'[:255],
            *window, [link_type(target, label[:255], f"/plantings/garden-register/{row['key']}")],
            {'garden_target': row['key'], 'anchor_start': window[0].isoformat(),
             'anchor_end': window[1].isoformat(), 'date_is_approximate': row['date_is_approximate']},
        )
        if task:
            tasks.append(task)
    return tasks
