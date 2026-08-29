"""Household-oriented projection of current and historical garden plants.

Unlike the nursery register, one row is not necessarily one ``SpecificPlant``.
An aggregate ``GardenPlanting`` remains one row carrying its quantity, while an
individually tracked origin is represented only by its plants.  Keeping that
choice here prevents the origin and the plants it created being counted twice.
"""

# The legacy row normalizer receives the fields shared by three unrelated
# source models; a parameter object would only move those names elsewhere.
# pylint: disable=too-many-arguments,too-many-positional-arguments

from datetime import datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from django.db.models import Exists, OuterRef
from django.contrib.contenttypes.models import ContentType
from rest_framework.exceptions import ValidationError

from health.models import HealthAffectedStock
from inventory.rest_query import parse_boolean, parse_date, parse_integer
from locations.models import Location, location_full_name
from plants.metadata import variety_days
from plants.models import MaturityBasis
from work.models import WorkTask, WorkTaskLink

from .lifecycle import FINAL_STATES, LifecycleState, with_lifecycle_state
from .models import (
    GardenPlanting, GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting,
    GardenSquareTransplant, ProductionBatch, SpecificPlant, SpecificPlantLocation,
)


SOURCE_VALUES = set(GardenPlanting.Source.values)
STATE_VALUES = {'current', 'finished', 'all'} | {
    value for value, _label in LifecycleState.choices
}
ORDERINGS = {'planted', 'crop', 'location', 'expected_harvest'}


class GardenRegisterFilters(NamedTuple):
    """One validated filter set shared by rows and summary counts."""

    crop: object = None
    variety: object = None
    location: str = ''
    source: str = ''
    state: str = 'current'
    planted_from: object = None
    planted_to: object = None
    expected_harvest_from: object = None
    expected_harvest_to: object = None
    health: object = None
    next_task: object = None
    search: str = ''
    ordering: str = 'crop'


def parse_garden_register_filters(params):
    """Validate the public query parameters before projecting any records."""
    source = params.get('source') or ''
    if source and source not in SOURCE_VALUES:
        raise ValidationError({'source': 'Select a valid planting source.'})
    state = params.get('state') or 'current'
    if state not in STATE_VALUES:
        raise ValidationError({'state': 'Select a valid garden state.'})
    ordering = params.get('ordering') or 'crop'
    if ordering.lstrip('-') not in ORDERINGS:
        raise ValidationError({'ordering': 'Select a valid ordering.'})
    location = (params.get('location') or '').strip()
    if location and location != 'unplaced':
        try:
            kind, identifier = location.split(':', 1)
            if kind not in {'square', 'location'} or int(identifier) <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValidationError({'location': 'Select a valid garden location.'}) from exc
    return GardenRegisterFilters(
        crop=parse_integer(params.get('crop'), 'crop'),
        variety=parse_integer(params.get('variety'), 'variety'),
        location=location,
        source=source,
        state=state,
        planted_from=parse_date(params.get('planted_from'), 'planted_from'),
        planted_to=parse_date(params.get('planted_to'), 'planted_to'),
        expected_harvest_from=parse_date(params.get('expected_harvest_from'), 'expected_harvest_from'),
        expected_harvest_to=parse_date(params.get('expected_harvest_to'), 'expected_harvest_to'),
        health=parse_boolean(params.get('health'), 'health'),
        next_task=parse_boolean(params.get('next_task'), 'next_task'),
        search=(params.get('search') or '').strip().casefold(),
        ordering=ordering,
    )


def _as_local_date(value, workspace):
    if value is None or not isinstance(value, datetime):
        return value
    return value.astimezone(ZoneInfo(workspace.timezone)).date()


def _expected_harvest(variety, planted_on, transplanted_on=None):
    """Return catalog maturity bounds without claiming missing metadata."""
    anchor = planted_on
    if variety.effective_maturity_basis == MaturityBasis.TRANSPLANTING:
        anchor = transplanted_on
    minimum, maximum = variety_days(variety, 'maturity')
    if anchor is None or minimum is None or maximum is None:
        return None, None
    return anchor + timedelta(days=minimum), anchor + timedelta(days=maximum)


def _location_values(location, names):
    if location is None:
        return 'unplaced', 'Unplaced', False
    if location.location_type == SpecificPlantLocation.GARDEN_SQUARE:
        return f'square:{location.garden_square_id}', str(location.garden_square), False
    if location.location_type == SpecificPlantLocation.LOCATION:
        place = location.location
        return (
            f'location:{place.pk}',
            location_full_name(place, names),
            place.location_type == Location.LocationType.CONTAINER,
        )
    return 'unplaced', 'Unplaced', False


def _aggregate_rows(workspace, location_names):
    entries = (
        GardenPlanting.objects
        .filter(workspace=workspace, tracking=GardenPlanting.Tracking.AGGREGATE)
        .select_related('batch__variety__plant', 'garden_square', 'location')
        .prefetch_related('status_events__reversal')
    )
    rows = []
    for entry in entries:
        variety = entry.batch.variety
        if entry.garden_square_id:
            location_key = f'square:{entry.garden_square_id}'
            location_label = str(entry.garden_square)
            container = False
        else:
            location_key = f'location:{entry.location_id}'
            location_label = location_full_name(entry.location, location_names)
            container = entry.location.location_type == Location.LocationType.CONTAINER
        expected_early, expected_late = _expected_harvest(variety, entry.recorded_on)
        effective_events = [
            event for event in entry.status_events.all()
            if event.event_type != event.EventType.CORRECTED and not hasattr(event, 'reversal')
        ]
        latest_event = effective_events[-1] if effective_events else None
        state = latest_event.event_type if latest_event else ('finished' if entry.finished_on else 'current')
        rows.append({
            'key': f'aggregate-{entry.pk}', 'record_type': 'aggregate', 'target_kind': 'aggregate', 'record_id': entry.pk,
            'plant': variety.plant_id, 'plant_name': variety.plant.name,
            'variety': variety.pk, 'variety_name': variety.name,
            'batch': entry.batch_id, 'batch_code': entry.batch.code,
            'name': '', 'source': entry.source, 'state': state,
            'quantity': entry.quantity, 'quantity_is_approximate': entry.quantity_is_approximate,
            'perennial': entry.perennial, 'container': container,
            'planted_on': entry.recorded_on, 'date_is_approximate': entry.date_is_approximate,
            'location': location_key, 'location_label': location_label,
            'expected_harvest_early': expected_early, 'expected_harvest_late': expected_late,
            'health_flag': False, 'next_task': None, 'finished_on': entry.finished_on,
        })
    return rows


def _individual_rows(workspace, location_names):  # pylint: disable=too-many-locals
    health = HealthAffectedStock.objects.filter(
        plant=OuterRef('pk'), observation__correction__isnull=True,
    )
    plants = (
        with_lifecycle_state(
            SpecificPlant.objects.filter(workspace=workspace)
            .select_related(
                'batch__variety__plant', 'garden_planting',
                'cell_planting__seed_tray_planting',
            )
            .prefetch_related(
                'locations__garden_square', 'locations__location',
            )
        )
        .annotate(has_health_flag=Exists(health))
    )
    rows = []
    for plant in plants:
        current_location = next((item for item in plant.locations.all() if item.ended is None), None)
        location_key, location_label, container = _location_values(current_location, location_names)
        origin = plant.garden_planting
        if origin:
            source = origin.source
            planted_on = origin.recorded_on
            perennial = origin.perennial
            date_is_approximate = origin.date_is_approximate
        elif plant.cell_planting_id:
            source = GardenPlanting.Source.INDOOR_RAISED_SEED
            planted_on = _as_local_date(plant.cell_planting.seed_tray_planting.planted, workspace)
            perennial = False
            date_is_approximate = False
        else:
            source = GardenPlanting.Source.EXISTING_UNKNOWN
            planted_on = _as_local_date(plant.germinated, workspace)
            perennial = False
            date_is_approximate = False
        transplanted_on = _as_local_date(current_location.started, workspace) if current_location else None
        expected_early, expected_late = _expected_harvest(
            plant.batch.variety, planted_on, transplanted_on,
        )
        rows.append({
            'key': f'individual-{plant.pk}', 'record_type': 'individual', 'target_kind': 'individual', 'record_id': plant.pk,
            'plant': plant.batch.variety.plant_id, 'plant_name': plant.batch.variety.plant.name,
            'variety': plant.batch.variety_id, 'variety_name': plant.batch.variety.name,
            'batch': plant.batch_id, 'batch_code': plant.batch.code,
            'name': plant.name, 'source': source, 'state': plant.lifecycle_state,
            'quantity': 1, 'quantity_is_approximate': False, 'perennial': perennial,
            'container': container, 'planted_on': planted_on,
            'date_is_approximate': date_is_approximate,
            'location': location_key, 'location_label': location_label,
            'expected_harvest_early': expected_early, 'expected_harvest_late': expected_late,
            'health_flag': plant.has_health_flag, 'next_task': None,
            'finished_on': _as_local_date(plant.final_outcome_at, workspace),
        })
    return rows


def _legacy_aggregate_rows(workspace):  # pylint: disable=too-many-locals
    """Project pre-quick-add sowings while suppressing represented transplants."""
    rows = []
    direct_sources = (
        ('direct-row', GardenRowDirectSowPlanting, 'row'),
        ('direct-square', GardenSquareDirectSowPlanting, 'square'),
    )
    for prefix, model, location_kind in direct_sources:
        entries = model.objects.filter(workspace=workspace).select_related(
            'batch__variety__plant', 'location',
        )
        for entry in entries:
            planted_on = _as_local_date(entry.planted, workspace)
            expected_early, expected_late = _expected_harvest(entry.batch.variety, planted_on)
            rows.append(_legacy_row(
                entry, prefix, location_kind, entry.location, entry.quantity,
                GardenPlanting.Source.DIRECT_SEED, planted_on,
                expected_early, expected_late,
            ))
    represented = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=workspace,
        specific_plant__cell_planting__seed_tray_planting_id=OuterRef('original_planting_id'),
    )
    transplants = (
        GardenSquareTransplant.objects.filter(workspace=workspace)
        .annotate(has_individuals=Exists(represented)).filter(has_individuals=False)
        .select_related('original_planting__batch__variety__plant', 'location')
    )
    for transplant in transplants:
        sowing = transplant.original_planting
        planted_on = _as_local_date(sowing.planted, workspace)
        transplanted_on = _as_local_date(transplant.transplanted, workspace)
        expected_early, expected_late = _expected_harvest(
            sowing.batch.variety, planted_on, transplanted_on,
        )
        rows.append(_legacy_row(
            transplant, 'transplant', 'square', transplant.location,
            transplant.quantity, GardenPlanting.Source.INDOOR_RAISED_SEED,
            planted_on, expected_early, expected_late, batch=sowing.batch,
        ))
    return rows


def _legacy_row(entry, prefix, location_kind, location, quantity, source, planted_on,
                expected_early, expected_late, batch=None):
    batch = batch or entry.batch
    return {
        'key': f'{prefix}-{entry.pk}', 'record_type': 'aggregate', 'target_kind': prefix, 'record_id': entry.pk,
        'plant': batch.variety.plant_id, 'plant_name': batch.variety.plant.name,
        'variety': batch.variety_id, 'variety_name': batch.variety.name,
        'batch': batch.pk, 'batch_code': batch.code, 'name': '', 'source': source,
        'state': 'finished' if entry.removed else 'current', 'quantity': quantity,
        'quantity_is_approximate': False, 'perennial': False, 'container': False,
        'planted_on': planted_on, 'date_is_approximate': False,
        'location': f'{location_kind}:{location.pk}', 'location_label': str(location),
        'expected_harvest_early': expected_early, 'expected_harvest_late': expected_late,
        'health_flag': False, 'next_task': None,
        'finished_on': None,
    }


def garden_register_projection(workspace):
    """Build all rows with a fixed number of database queries."""
    location_names = dict(
        Location.objects.filter(workspace=workspace).values_list('pk', 'name'),
    )
    rows = _aggregate_rows(workspace, location_names)
    rows.extend(_legacy_aggregate_rows(workspace))
    rows.extend(_individual_rows(workspace, location_names))
    _attach_next_tasks(workspace, rows)
    return rows


def _attach_next_tasks(workspace, rows):
    """Attach the earliest open task linked to a row or its planting cycle."""
    if not rows:
        return
    models = {
        'aggregate': GardenPlanting,
        'individual': SpecificPlant,
        'batch': ProductionBatch,
        'direct-row': GardenRowDirectSowPlanting,
        'direct-square': GardenSquareDirectSowPlanting,
        'transplant': GardenSquareTransplant,
    }
    content_types = {
        name: ContentType.objects.get_for_model(model).pk
        for name, model in models.items()
    }
    ids = {
        **{
            kind: {row['record_id'] for row in rows if row['target_kind'] == kind}
            for kind in models if kind != 'batch'
        },
        'batch': {row['batch'] for row in rows},
    }
    links = (
        WorkTaskLink.objects
        .filter(
            task__workspace=workspace,
            task__status__in=[WorkTask.Status.OPEN, WorkTask.Status.SNOOZED],
            role=WorkTaskLink.Role.TARGET,
        )
        .select_related('task')
        .order_by('task__due_end', '-task__priority', 'task_id')
    )
    task_by_target = {}
    for link in links:
        kind = next((name for name, pk in content_types.items() if pk == link.content_type_id), None)
        if kind and link.object_id in ids[kind]:
            task_by_target.setdefault((kind, link.object_id), {
                'id': link.task_id,
                'title': link.task.title,
                'due': link.task.due_end,
                'status': link.task.status,
                'url': link.url or '/work',
            })
    for row in rows:
        row['next_task'] = task_by_target.get(
            (row['target_kind'], row['record_id']),
        ) or task_by_target.get(('batch', row['batch']))


def _is_current(row):
    return row['state'] == 'current' or row['state'] not in FINAL_STATES | {'finished'}


def garden_register_rows(workspace, filters):  # pylint: disable=too-many-branches
    """Apply one filter set to both kinds of projected garden row."""
    rows = garden_register_projection(workspace)
    if filters.state == 'current':
        rows = [row for row in rows if _is_current(row)]
    elif filters.state == 'finished':
        rows = [row for row in rows if not _is_current(row)]
    elif filters.state != 'all':
        rows = [row for row in rows if row['state'] == filters.state]
    if filters.crop is not None:
        rows = [row for row in rows if row['plant'] == filters.crop]
    if filters.variety is not None:
        rows = [row for row in rows if row['variety'] == filters.variety]
    if filters.location:
        rows = [row for row in rows if row['location'] == filters.location]
    if filters.source:
        rows = [row for row in rows if row['source'] == filters.source]
    if filters.planted_from:
        rows = [row for row in rows if row['planted_on'] >= filters.planted_from]
    if filters.planted_to:
        rows = [row for row in rows if row['planted_on'] <= filters.planted_to]
    if filters.expected_harvest_from:
        rows = [row for row in rows if row['expected_harvest_late'] and row['expected_harvest_late'] >= filters.expected_harvest_from]
    if filters.expected_harvest_to:
        rows = [row for row in rows if row['expected_harvest_early'] and row['expected_harvest_early'] <= filters.expected_harvest_to]
    if filters.health is not None:
        rows = [row for row in rows if row['health_flag'] is filters.health]
    if filters.next_task is not None:
        rows = [row for row in rows if (row['next_task'] is not None) is filters.next_task]
    if filters.search:
        rows = [row for row in rows if filters.search in ' '.join((
            row['plant_name'], row['variety_name'], row['name'], row['batch_code'], row['location_label'],
        )).casefold()]
    field = {
        'planted': 'planted_on', 'crop': 'plant_name', 'location': 'location_label',
        'expected_harvest': 'expected_harvest_early',
    }[filters.ordering.lstrip('-')]
    rows.sort(key=lambda row: (row[field] is None, row[field], row['key']), reverse=filters.ordering.startswith('-'))
    return rows


def garden_register_totals(rows):
    """Summarize exactly the already-filtered rows, independent of paging."""
    return {
        'rows': len(rows),
        'quantity': sum(row['quantity'] for row in rows),
        'aggregate_rows': sum(row['record_type'] == 'aggregate' for row in rows),
        'individual_plants': sum(row['record_type'] == 'individual' for row in rows),
        'perennials': sum(row['perennial'] for row in rows),
        'containers': sum(row['container'] for row in rows),
        'unplaced': sum(row['location'] == 'unplaced' for row in rows),
        'health_flags': sum(row['health_flag'] for row in rows),
    }
