"""Project authoritative nursery facts into unacknowledged work occurrences."""

# Projection records intentionally carry the complete API-facing task snapshot,
# and projector functions keep source-specific facts together for auditability.
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef
from django.utils import timezone

from plantings.growth import current_growth
from plantings.models import (
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    NurseryPlanMilestone,
    NurseryProductionPlan,
    PlantCohort,
    ProductionBatch,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from plants.metadata import variety_days
from plants.models import MaturityBasis

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
    return variety_days(variety, prefix)


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
        if maturity and variety.effective_maturity_basis != MaturityBasis.SEED:
            continue
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


def _direct_sow_maturity_tasks(rule):
    """Project direct garden sowings from their sowing dates in every case."""
    tasks = []
    sources = (
        (GardenRowDirectSowPlanting, 'row'),
        (GardenSquareDirectSowPlanting, 'square'),
    )
    for model, label in sources:
        rows = model.objects.filter(
            workspace=rule.workspace,
            removed=False,
        ).select_related('batch__variety__plant', 'location')
        for sowing in rows:
            variety = sowing.batch.variety
            minimum, maximum = _metadata_days(variety, 'maturity')
            if minimum is None or maximum is None or not _allows(rule, variety=variety):
                continue
            planted = sowing.planted.astimezone(
                ZoneInfo(rule.workspace.timezone)
            ).date()
            tasks.append(_source_task(
                rule,
                f'maturity:direct-{label}:{sowing.pk}',
                f'Harvest review: {variety}',
                planted + timedelta(days=minimum),
                planted + timedelta(days=maximum),
                [TargetLink(sowing, f'Direct sowing {sowing.pk}')],
                {
                    'sowing': sowing.pk,
                    'planted': sowing.planted.isoformat(),
                    'maturity_basis': MaturityBasis.SEED,
                },
            ))
    return [task for task in tasks if task]


def _transplant_maturity_tasks(rule):
    """Project transplant-based maturity only from active garden-square placements."""
    tasks = []
    represented_sowings = set(
        SpecificPlantLocation.objects.filter(
            specific_plant__workspace=rule.workspace,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
        ).values_list(
            'specific_plant__cell_planting__seed_tray_planting_id', flat=True,
        )
    )
    aggregate_rows = GardenSquareTransplant.objects.filter(
        workspace=rule.workspace,
        removed=False,
    ).exclude(
        original_planting_id__in=represented_sowings,
    ).select_related(
        'original_planting__batch__variety__plant', 'location',
    )
    for transplant in aggregate_rows:
        variety = transplant.original_planting.batch.variety
        minimum, maximum = _metadata_days(variety, 'maturity')
        if not all((
            variety.effective_maturity_basis == MaturityBasis.TRANSPLANTING,
            minimum is not None,
            maximum is not None,
            _allows(rule, variety=variety),
        )):
            continue
        planted_out = transplant.transplanted.astimezone(
            ZoneInfo(rule.workspace.timezone)
        ).date()
        tasks.append(_source_task(
            rule,
            f'maturity:transplant:{transplant.pk}',
            f'Harvest review: {variety}',
            planted_out + timedelta(days=minimum),
            planted_out + timedelta(days=maximum),
            [TargetLink(transplant, f'Transplant {transplant.pk}')],
            {
                'transplant': transplant.pk,
                'transplanted': transplant.transplanted.isoformat(),
                'maturity_basis': MaturityBasis.TRANSPLANTING,
            },
        ))

    individual_rows = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=rule.workspace,
        location_type=SpecificPlantLocation.GARDEN_SQUARE,
        ended__isnull=True,
    ).select_related(
        'specific_plant__batch__variety__plant', 'garden_square',
    )
    for location in individual_rows:
        variety = location.specific_plant.batch.variety
        minimum, maximum = _metadata_days(variety, 'maturity')
        if not all((
            variety.effective_maturity_basis == MaturityBasis.TRANSPLANTING,
            minimum is not None,
            maximum is not None,
            _allows(rule, variety=variety),
        )):
            continue
        planted_out = location.started.astimezone(
            ZoneInfo(rule.workspace.timezone)
        ).date()
        plant = location.specific_plant
        tasks.append(_source_task(
            rule,
            f'maturity:plant:{plant.pk}',
            f'Harvest review: {variety}',
            planted_out + timedelta(days=minimum),
            planted_out + timedelta(days=maximum),
            [TargetLink(
                plant, f'Plant {plant.pk}', f'/plantings/plants/{plant.pk}',
            )],
            {
                'plant': plant.pk,
                'transplanted': location.started.isoformat(),
                'maturity_basis': MaturityBasis.TRANSPLANTING,
            },
        ))
    return [task for task in tasks if task]


def _maturity_tasks(rule):
    """Combine maturity projections for sowings and actual transplants."""
    return [
        *_sowing_tasks(rule, maturity=True),
        *_direct_sow_maturity_tasks(rule),
        *_transplant_maturity_tasks(rule),
    ]


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


def _health_target_links(observation):
    """Turn the frozen affected set into actionable work links."""
    links = []
    for member in observation.affected_stock.all():
        if member.plant_id:
            links.append(TargetLink(
                member.plant, f'Plant {member.plant_id}',
                f'/plantings/plants/{member.plant_id}',
            ))
        else:
            links.append(TargetLink(
                member.cohort,
                f'Cohort {member.cohort_id} ({member.quantity})',
                f'/plantings/cohorts/{member.cohort_id}',
            ))
    return links


def _health_follow_up_tasks(rule):
    """Project outstanding observation and treatment review dates."""
    from health.models import HealthFollowUp, HealthObservation, HealthTreatment  # pylint: disable=import-outside-toplevel

    observation_complete = HealthFollowUp.objects.filter(
        observation_id=OuterRef('pk'), treatment__isnull=True,
        correction__isnull=True,
    )
    treatment_complete = HealthFollowUp.objects.filter(
        treatment_id=OuterRef('pk'), correction__isnull=True,
    )
    observations = HealthObservation.objects.annotate(
        follow_up_complete=Exists(observation_complete),
    ).filter(
        workspace=rule.workspace, correction__isnull=True,
        follow_up_due_at__isnull=False, follow_up_complete=False,
    ).select_related('observation_type').prefetch_related(
        'affected_stock__plant', 'affected_stock__cohort',
    )
    treatments = HealthTreatment.objects.annotate(
        follow_up_complete=Exists(treatment_complete),
    ).filter(
        workspace=rule.workspace, follow_up_due_at__isnull=False,
        follow_up_complete=False,
    ).select_related(
        'observation__observation_type',
    ).prefetch_related(
        'observation__affected_stock__plant',
        'observation__affected_stock__cohort',
    )
    tasks = []
    for observation in observations.distinct():
        due = observation.follow_up_due_at.astimezone(
            ZoneInfo(rule.workspace.timezone),
        ).date()
        tasks.append(_source_task(
            rule, f'health-observation:{observation.pk}',
            f'Health follow-up: {observation.observation_type.name}',
            due, due, _health_target_links(observation),
            {'health_observation': observation.pk},
        ))
    for treatment in treatments.distinct():
        due = treatment.follow_up_due_at.astimezone(
            ZoneInfo(rule.workspace.timezone),
        ).date()
        tasks.append(_source_task(
            rule, f'health-treatment:{treatment.pk}',
            f'Treatment follow-up: {treatment.observation.observation_type.name}',
            due, due, _health_target_links(treatment.observation),
            {
                'health_observation': treatment.observation_id,
                'health_treatment': treatment.pk,
                'application': treatment.application_id,
            },
        ))
    return [task for task in tasks if task]


PROJECTORS = {
    WorkTaskRule.Trigger.GERMINATION: _sowing_tasks,
    WorkTaskRule.Trigger.MATURITY: _maturity_tasks,
    WorkTaskRule.Trigger.PLAN_MILESTONE: _milestone_tasks,
    WorkTaskRule.Trigger.STAGE_AGE: _growth_tasks,
    WorkTaskRule.Trigger.EXPECTED_READY: lambda rule: _growth_tasks(rule, expected_ready=True),
    WorkTaskRule.Trigger.HEALTH_FOLLOW_UP: _health_follow_up_tasks,
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
