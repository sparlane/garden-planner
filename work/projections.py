"""Project authoritative nursery facts into unacknowledged work occurrences."""

# Projection records intentionally carry the complete API-facing task snapshot,
# and projector functions keep source-specific facts together for auditability.
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from plantings.growth import current_growth
from plantings.models import (
    NurseryPlanMilestone,
    NurseryProductionPlan,
    PlantCohort,
    ProductionBatch,
    SeedTrayPlanting,
    SpecificPlant,
)

from .models import WorkTask, WorkTaskRule


OPEN_BATCHES = (ProductionBatch.Status.PLANNED, ProductionBatch.Status.ACTIVE)


@dataclass(frozen=True)
class TargetLink:
    """One live target attached to a projected occurrence."""

    target: object
    label: str
    url: str = ''


@dataclass(frozen=True)
class ProjectedTask:
    """A generated task that has not yet been acknowledged."""

    key: str
    rule: WorkTaskRule
    task_type: str
    title: str
    priority: int
    due_start: datetime
    due_end: datetime
    assignee: object
    targets: tuple[TargetLink, ...]
    source_snapshot: dict

    @property
    def origin(self):
        """Identify this task as generated work."""
        return WorkTask.Origin.GENERATED

    @property
    def status(self):
        """An unacknowledged projection is open by definition."""
        return WorkTask.Status.OPEN


def local_due(workspace, day, at_time):
    """Convert one local calendar time to an aware instant."""
    return datetime.combine(day, at_time, tzinfo=ZoneInfo(workspace.timezone))


def next_recurrence(workspace, after, frequency, interval=1, weekdays=()):
    """Advance in local calendar units, retaining wall time through DST."""
    local = after.astimezone(ZoneInfo(workspace.timezone))
    if frequency == WorkTaskRule.Frequency.DAILY:
        next_day = local.date() + timedelta(days=interval)
    elif frequency == WorkTaskRule.Frequency.WEEKLY:
        allowed = set(weekdays)
        next_day = local.date() + timedelta(days=1)
        while next_day.weekday() not in allowed:
            next_day += timedelta(days=1)
        if next_day.weekday() <= local.weekday():
            next_day += timedelta(weeks=max(interval - 1, 0))
    else:
        raise ValueError('A recurring task requires a supported frequency.')
    return local_due(workspace, next_day, local.timetz().replace(tzinfo=None))


def _metadata_days(variety, prefix):
    minimum = getattr(variety, f'{prefix}_days_min')
    maximum = getattr(variety, f'{prefix}_days_max')
    plant = variety.plant
    return minimum or getattr(plant, f'{prefix}_days_min'), maximum or getattr(plant, f'{prefix}_days_max')


def _window(rule, start_day, end_day=None):
    end_day = end_day or start_day
    return (
        local_due(rule.workspace, start_day + timedelta(days=rule.due_start_offset_days), rule.local_due_time),
        local_due(rule.workspace, end_day + timedelta(days=rule.due_end_offset_days), rule.local_due_time),
    )


def _in_season(rule, day):
    if not rule.season_start:
        return True
    value = day.strftime('%m-%d')
    if rule.season_start <= rule.season_end:
        return rule.season_start <= value <= rule.season_end
    return value >= rule.season_start or value <= rule.season_end


def _allows(rule, variety=None, stage=None, location=None):
    return all((
        rule.variety_id is None or (variety and rule.variety_id == variety.pk),
        rule.stage_id is None or (stage and rule.stage_id == stage.pk),
        rule.location_id is None or (location and location.path.startswith(rule.location.path)),
    ))


def _source_task(rule, key, title, start_day, end_day, targets, snapshot):
    due_start, due_end = _window(rule, start_day, end_day)
    if not _in_season(rule, due_end.astimezone(ZoneInfo(rule.workspace.timezone)).date()):
        return None
    return ProjectedTask(
        key=f'rule:{rule.pk}:{key}', rule=rule, task_type=rule.task_type,
        title=title, priority=rule.priority, due_start=due_start, due_end=due_end,
        assignee=rule.default_assignee, targets=tuple(targets), source_snapshot=snapshot,
    )


def _sowing_tasks(rule, maturity=False):
    prefix = 'maturity' if maturity else 'germination'
    label = 'Harvest review' if maturity else 'Germination check'
    rows = SeedTrayPlanting.objects.filter(
        workspace=rule.workspace, removed=False, batch__status__in=OPEN_BATCHES,
    ).select_related('batch__variety__plant', 'seed_tray')
    tasks = []
    for sowing in rows:
        variety = sowing.batch.variety
        minimum, maximum = _metadata_days(variety, prefix)
        if minimum is None or maximum is None or not _allows(rule, variety=variety):
            continue
        planted = sowing.planted.astimezone(ZoneInfo(rule.workspace.timezone)).date()
        start_day, end_day = planted + timedelta(days=minimum), planted + timedelta(days=maximum)
        targets = [TargetLink(sowing, f'Sowing {sowing.pk}')]
        if sowing.seed_tray_id:
            targets.append(TargetLink(sowing.seed_tray, f'Tray {sowing.seed_tray_id}', f'/seedtrays/{sowing.seed_tray_id}'))
        tasks.append(_source_task(
            rule, f'sowing:{sowing.pk}', f'{label}: {variety}', start_day, end_day,
            targets, {'sowing': sowing.pk, 'planted': sowing.planted.isoformat()},
        ))
    return [task for task in tasks if task]


def _milestone_tasks(rule):
    rows = NurseryPlanMilestone.objects.filter(
        requirement__demand__plan__workspace=rule.workspace,
        requirement__demand__plan__status=NurseryProductionPlan.Status.APPROVED,
    ).select_related(
        'stage', 'location', 'requirement__batch',
        'requirement__demand__variety', 'requirement__demand__plan',
    )
    tasks = []
    for milestone in rows:
        demand = milestone.requirement.demand
        if not _allows(rule, demand.variety, milestone.stage, milestone.location):
            continue
        targets = [TargetLink(
            milestone, f'{demand.plan} · {milestone.stage.name}',
            '/plantings/production-planning',
        )]
        if milestone.requirement.batch_id:
            batch = milestone.requirement.batch
            targets.append(TargetLink(batch, f'Batch {batch.code}', f'/plantings/batches/{batch.pk}'))
        if milestone.location_id:
            targets.append(TargetLink(milestone.location, milestone.location.full_name, '/locations'))
        tasks.append(_source_task(
            rule, f'milestone:{milestone.pk}',
            f'{milestone.stage.name}: {demand.variety}',
            milestone.planned_date, milestone.planned_date, targets,
            {'milestone': milestone.pk, 'planned_date': milestone.planned_date.isoformat()},
        ))
    return [task for task in tasks if task]


def _plant_location(plant):
    placement = next((row for row in plant.locations.all() if row.ended is None), None)
    if placement is None:
        return None
    if placement.location_id:
        return placement.location
    if placement.seed_tray_cell_id:
        return placement.seed_tray_cell.tray.inventory_unit.current_location
    return None


def _growth_candidates(rule):
    plants = SpecificPlant.objects.filter(
        workspace=rule.workspace, batch__status__in=OPEN_BATCHES,
    ).select_related('batch__variety__plant').prefetch_related(
        'locations__location', 'locations__seed_tray_cell__tray__inventory_unit__current_location',
        'nursery_observation_targets__observation__stage',
    )
    cohorts = PlantCohort.objects.filter(
        workspace=rule.workspace, quantity__gt=0, batch__status__in=OPEN_BATCHES,
    ).select_related('batch__variety__plant', 'location').prefetch_related(
        'nursery_observation_targets__observation__stage',
    )
    for target in list(plants) + list(cohorts):
        growth = current_growth(target)
        location = target.location if isinstance(target, PlantCohort) else _plant_location(target)
        yield target, target.batch, location, growth


def _growth_tasks(rule, expected_ready=False):
    grouped = {}
    for target, batch, location, growth in _growth_candidates(rule):
        stage = growth['stage']
        if not _allows(rule, batch.variety, stage, location):
            continue
        if expected_ready:
            day = growth['expected_ready']
        else:
            if stage is None or stage.target_days is None or growth['stage_observed_at'] is None:
                continue
            local_observed = growth['stage_observed_at'].astimezone(ZoneInfo(rule.workspace.timezone)).date()
            day = local_observed + timedelta(days=stage.target_days)
        if day is None:
            continue
        group_key = (batch.pk, location.pk if location else 0, day, stage.pk if stage else 0)
        grouped.setdefault(group_key, []).append(target)
    tasks = []
    for (batch_id, location_id, day, stage_id), targets in grouped.items():
        batch = targets[0].batch
        location = targets[0].location if isinstance(targets[0], PlantCohort) else _plant_location(targets[0])
        links = [TargetLink(batch, f'Batch {batch.code}', f'/plantings/batches/{batch.pk}')]
        if location:
            links.append(TargetLink(location, location.full_name, '/locations'))
        for target in targets:
            kind = 'cohorts' if isinstance(target, PlantCohort) else 'plants'
            links.append(TargetLink(target, f'{kind[:-1].title()} {target.pk}', f'/plantings/{kind}/{target.pk}'))
        title = f'{"Ready-date" if expected_ready else "Stage"} review: {batch.code}'
        key = f'group:{batch_id}:{location_id}:{stage_id}:{day.isoformat()}'
        tasks.append(_source_task(
            rule, key, title, day, day, links,
            {'batch': batch_id, 'location': location_id or None, 'target_count': len(targets)},
        ))
    return [task for task in tasks if task]


def _calendar_day(rule, today):
    created = rule.created.astimezone(ZoneInfo(rule.workspace.timezone)).date()
    if rule.frequency == WorkTaskRule.Frequency.DAILY:
        elapsed = max((today - created).days, 0)
        return created + timedelta(days=(elapsed // rule.interval) * rule.interval)
    candidates = [today - timedelta(days=offset) for offset in range(0, 14 * rule.interval)]
    return next((day for day in candidates if day.weekday() in rule.weekdays), created)


def _calendar_tasks(rule, today):
    day = _calendar_day(rule, today)
    grouped = {}
    for target, batch, location, growth in _growth_candidates(rule):
        if not _allows(rule, batch.variety, growth['stage'], location):
            continue
        key = (batch.pk, location.pk if location else 0)
        grouped.setdefault(key, []).append(target)
    tasks = []
    for (batch_id, location_id), targets in grouped.items():
        links = [TargetLink(target, f'Target {target.pk}') for target in targets]
        tasks.append(_source_task(
            rule, f'calendar:{batch_id}:{location_id}:{day.isoformat()}',
            f'{rule.name}: {targets[0].batch.code}', day, day, links,
            {'occurrence': day.isoformat(), 'target_count': len(targets)},
        ))
    return [task for task in tasks if task]


PROJECTORS = {
    WorkTaskRule.Trigger.GERMINATION: _sowing_tasks,
    WorkTaskRule.Trigger.MATURITY: lambda rule: _sowing_tasks(rule, maturity=True),
    WorkTaskRule.Trigger.PLAN_MILESTONE: _milestone_tasks,
    WorkTaskRule.Trigger.STAGE_AGE: _growth_tasks,
    WorkTaskRule.Trigger.EXPECTED_READY: lambda rule: _growth_tasks(rule, expected_ready=True),
}


def projected_tasks(workspace, today=None):
    """Return current projections, suppressing occurrences already acknowledged."""
    today = today or timezone.now().astimezone(ZoneInfo(workspace.timezone)).date()
    tasks = []
    rules = WorkTaskRule.objects.filter(workspace=workspace, active=True).select_related(
        'workspace', 'variety', 'stage', 'location', 'default_assignee',
    )
    for rule in rules:
        if rule.trigger == WorkTaskRule.Trigger.CALENDAR:
            tasks.extend(_calendar_tasks(rule, today))
        else:
            tasks.extend(PROJECTORS[rule.trigger](rule))
    acknowledged = set(WorkTask.objects.filter(
        workspace=workspace, key__in=[task.key for task in tasks],
    ).values_list('key', flat=True))
    return sorted(
        (task for task in tasks if task.key not in acknowledged),
        key=lambda task: (task.due_end, -task.priority, task.key),
    )


def target_identity(target):
    """Return the stable generic identity used when acknowledging a target."""
    return ContentType.objects.get_for_model(target, for_concrete_model=True), target.pk
