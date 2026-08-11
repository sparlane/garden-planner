"""Audited nursery growth observations and current-value projections."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import NurseryObservation, NurseryObservationTarget, PlantCohort, SpecificPlant


OBSERVED_FIELDS = (
    'stage', 'grade', 'container_item', 'height_cm', 'spread_cm',
    'root_condition', 'expected_ready', 'notes',
)


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _target_rows(workspace, plant_ids=(), cohort_id=None, lock=False):
    """Resolve one homogeneous target set without accepting foreign IDs."""
    if bool(plant_ids) == bool(cohort_id):
        raise ValidationError({'targets': 'Choose plants or one cohort.'})
    if plant_ids:
        ids = sorted(set(plant_ids))
        queryset = SpecificPlant.objects.filter(workspace=workspace, pk__in=ids).order_by('pk')
        if lock:
            queryset = queryset.select_for_update()
        targets = list(queryset)
        if len(targets) != len(ids):
            raise ValidationError({'plants': 'One or more plants do not belong to this workspace.'})
        return targets
    queryset = PlantCohort.objects.filter(workspace=workspace, pk=cohort_id)
    if lock:
        queryset = queryset.select_for_update()
    cohort = queryset.first()
    if cohort is None:
        raise ValidationError({'cohort': 'The cohort does not belong to this workspace.'})
    return [cohort]


def _snapshot_container(values):
    item = values.get('container_item')
    if item is None:
        return
    values.update({
        'container_name': item.name,
        'container_size_label': item.container_size_label,
        'container_volume_ml': item.container_volume_ml,
        'container_footprint_m2': item.container_footprint_m2,
    })


@transaction.atomic
def record_observation(workspace, user, *, plant_ids=(), cohort_id=None, **values):
    """Append one fact shared by explicitly resolved plants or one cohort."""
    targets = _target_rows(workspace, plant_ids, cohort_id, lock=True)
    _snapshot_container(values)
    observation = NurseryObservation.objects.create(
        workspace=workspace,
        created_by=_actor(user),
        **values,
    )
    NurseryObservationTarget.objects.bulk_create([
        NurseryObservationTarget(
            observation=observation,
            plant=target if isinstance(target, SpecificPlant) else None,
            cohort=target if isinstance(target, PlantCohort) else None,
        )
        for target in targets
    ])
    return observation


@transaction.atomic
def correct_observation(workspace, user, *, observation_id, **values):
    """Neutralize a whole observation by appending a replacement fact."""
    original = (
        NurseryObservation.objects.select_for_update()
        .prefetch_related('targets')
        .filter(workspace=workspace, pk=observation_id)
        .first()
    )
    if original is None:
        raise ValidationError({'corrects': 'The observation does not belong to this workspace.'})
    if hasattr(original, 'correction'):
        raise ValidationError({'corrects': 'This observation has already been corrected.'})
    targets = list(original.targets.all())
    _snapshot_container(values)
    replacement = NurseryObservation.objects.create(
        workspace=workspace,
        created_by=_actor(user),
        corrects=original,
        **values,
    )
    NurseryObservationTarget.objects.bulk_create([
        NurseryObservationTarget(
            observation=replacement,
            plant_id=target.plant_id,
            cohort_id=target.cohort_id,
        )
        for target in targets
    ])
    return replacement


def effective_observations(target):
    """Return uncorrected observations for one target, newest first."""
    lookup = 'plant' if isinstance(target, SpecificPlant) else 'cohort'
    return (
        NurseryObservation.objects
        .filter(**{f'targets__{lookup}': target}, correction__isnull=True)
        .select_related('stage', 'grade', 'container_item', 'created_by')
        .order_by('-occurred_at', '-pk')
    )


def current_growth(target):
    """Project each current fact independently from append-only history."""
    rows = list(effective_observations(target))
    current = {field: None for field in OBSERVED_FIELDS}
    current['stage_observed_at'] = None
    current['container_count'] = None
    current['container_name'] = ''
    current['container_size_label'] = ''
    current['container_footprint_m2'] = None
    for row in rows:
        for field in OBSERVED_FIELDS:
            if current[field] is None and getattr(row, field) not in (None, ''):
                current[field] = getattr(row, field)
                if field == 'stage':
                    current['stage_observed_at'] = row.occurred_at
                if field == 'container_item':
                    current.update({
                        'container_count': row.container_count,
                        'container_name': row.container_name,
                        'container_size_label': row.container_size_label,
                        'container_footprint_m2': row.container_footprint_m2,
                    })
        if all(current[field] is not None for field in OBSERVED_FIELDS):
            break
    return current
