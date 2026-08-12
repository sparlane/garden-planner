"""Resolve reviewed scopes and append immutable health observations."""

# Observation commands carry the reviewed set plus their immutable evidence.
# pylint: disable=too-many-arguments

from hashlib import sha256

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from locations.models import Location
from plantings.lifecycle import FINAL_STATES, with_lifecycle_state
from plantings.models import PlantCohort, ProductionBatch, SpecificPlant
from seedtrays.models import SeedTray, SeedTrayGeneration

from .models import (
    HealthAffectedStock,
    HealthEvidenceLink,
    HealthObservation,
    HealthObservationDiagnosis,
    HealthObservationScope,
)


SCOPE_MODELS = {
    'plant': SpecificPlant,
    'cohort': PlantCohort,
    'generation': SeedTrayGeneration,
    'batch': ProductionBatch,
    'location': Location,
}


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _scope_target(workspace, target_type, target_id):
    """Resolve one selected scope without leaking foreign-workspace objects."""
    if target_type == 'tray':
        tray = SeedTray.objects.filter(workspace=workspace, pk=target_id).first()
        if tray is None:
            raise ValidationError({'scopes': 'The tray does not belong to this workspace.'})
        generation = tray.generations.filter(status=SeedTrayGeneration.Status.OPEN).first()
        if generation is None:
            raise ValidationError({'scopes': 'The tray has no open fill to inspect.'})
        return 'generation', generation
    model = SCOPE_MODELS.get(target_type)
    if model is None:
        raise ValidationError({'scopes': f'Unsupported health scope: {target_type}.'})
    target = model.objects.filter(workspace=workspace, pk=target_id).first()
    if target is None:
        raise ValidationError({'scopes': 'One or more scopes do not belong to this workspace.'})
    return target_type, target


def _live_plants(workspace):
    return with_lifecycle_state(
        SpecificPlant.objects.filter(workspace=workspace),
    ).exclude(lifecycle_state__in=sorted(FINAL_STATES))


def _members_for_scope(workspace, target_type, target):
    plants = _live_plants(workspace)
    cohorts = PlantCohort.objects.filter(workspace=workspace, quantity__gt=0)
    if target_type == 'plant':
        plants = plants.filter(pk=target.pk)
        cohorts = cohorts.none()
    elif target_type == 'cohort':
        plants = plants.none()
        cohorts = cohorts.filter(pk=target.pk)
    elif target_type == 'generation':
        plants = plants.filter(cell_planting__seed_tray_planting__generation=target)
        cohorts = cohorts.filter(source_sowing__generation=target)
    elif target_type == 'batch':
        plants = plants.filter(batch=target)
        cohorts = cohorts.filter(batch=target)
    elif target_type == 'location':
        location_ids = target.subtree().values('pk')
        direct = Q(locations__ended__isnull=True, locations__location_id__in=location_ids)
        tray = Q(
            locations__ended__isnull=True,
            locations__seed_tray_cell__tray__inventory_unit__current_location_id__in=location_ids,
        )
        plants = plants.filter(direct | tray).distinct()
        cohorts = cohorts.filter(location_id__in=location_ids)
    return plants.values_list('pk', flat=True), cohorts.values_list('pk', 'quantity')


def resolve_scopes(workspace, scopes):
    """Resolve source scopes and freeze their union of current living stock."""
    if not scopes:
        raise ValidationError({'scopes': 'Select at least one scope.'})
    resolved = []
    plant_ids = set()
    cohorts = {}
    scope_keys = set()
    for raw in scopes:
        target_type, target = _scope_target(workspace, raw['type'], raw['id'])
        key = (target_type, target.pk)
        if key in scope_keys:
            continue
        scope_keys.add(key)
        resolved.append((target_type, target))
        plants, cohort_rows = _members_for_scope(workspace, target_type, target)
        plant_ids.update(plants)
        cohorts.update(dict(cohort_rows))
    return resolved, sorted(plant_ids), sorted(cohorts.items())


def affected_digest(resolved, plant_ids, cohorts):
    """Hash the reviewed source identities and exact stock membership."""
    parts = [f's:{kind}:{target.pk}' for kind, target in resolved]
    parts.extend(f'p:{plant_id}' for plant_id in plant_ids)
    parts.extend(f'c:{cohort_id}:{quantity}' for cohort_id, quantity in cohorts)
    return sha256('\n'.join(parts).encode()).hexdigest()


def preview_observation(workspace, scopes):
    """Describe the exact set a confirmation would record right now."""
    resolved, plant_ids, cohorts = resolve_scopes(workspace, scopes)
    return {
        'scopes': [
            {'type': kind, 'id': target.pk, 'label': str(target)}
            for kind, target in resolved
        ],
        'plants': plant_ids,
        'cohorts': [
            {'cohort': cohort_id, 'quantity': quantity}
            for cohort_id, quantity in cohorts
        ],
        'affected_count': len(plant_ids) + sum(quantity for _, quantity in cohorts),
        'digest': affected_digest(resolved, plant_ids, cohorts),
    }


def _validate_diagnoses(workspace, diagnoses):
    for diagnosis, _certainty in diagnoses:
        if diagnosis.workspace_id != workspace.pk:
            raise ValidationError({'diagnoses': 'Choose diagnoses from this workspace.'})


@transaction.atomic
def record_observation(
        workspace, user, *, scopes, reviewed_digest, diagnoses=(), evidence=(), **values,
):
    """Append an observation only if its reviewed affected set is still current."""
    resolved, plant_ids, cohorts = resolve_scopes(workspace, scopes)
    digest = affected_digest(resolved, plant_ids, cohorts)
    if digest != reviewed_digest:
        raise ValidationError({'reviewed_digest': 'The affected stock changed; review it again.'})
    _validate_diagnoses(workspace, diagnoses)
    observation = HealthObservation.objects.create(
        workspace=workspace, created_by=_actor(user), **values,
    )
    HealthObservationScope.objects.bulk_create([
        HealthObservationScope(
            observation=observation,
            label=str(target)[:255],
            **{target_type: target},
        )
        for target_type, target in resolved
    ])
    HealthAffectedStock.objects.bulk_create([
        HealthAffectedStock(observation=observation, plant_id=plant_id, quantity=1)
        for plant_id in plant_ids
    ] + [
        HealthAffectedStock(
            observation=observation, cohort_id=cohort_id, quantity=quantity,
        )
        for cohort_id, quantity in cohorts
    ])
    HealthObservationDiagnosis.objects.bulk_create([
        HealthObservationDiagnosis(
            observation=observation, diagnosis=diagnosis, certainty=certainty,
        )
        for diagnosis, certainty in diagnoses
    ])
    HealthEvidenceLink.objects.bulk_create([
        HealthEvidenceLink(observation=observation, **item) for item in evidence
    ])
    return observation


@transaction.atomic
def correct_observation(
        workspace, user, original, *, diagnoses=(), evidence=(), **values,
):
    """Append replacement evidence over the original immutable snapshot."""
    original = HealthObservation.objects.select_for_update().filter(
        workspace=workspace, pk=original.pk,
    ).first()
    if original is None:
        raise ValidationError({'corrects': 'The observation does not belong to this workspace.'})
    if hasattr(original, 'correction'):
        raise ValidationError({'corrects': 'This observation has already been corrected.'})
    _validate_diagnoses(workspace, diagnoses)
    replacement = HealthObservation.objects.create(
        workspace=workspace, created_by=_actor(user), corrects=original, **values,
    )
    HealthObservationScope.objects.bulk_create([
        HealthObservationScope(
            observation=replacement, label=row.label,
            **{
                field: getattr(row, field)
                for field in HealthObservationScope.TARGET_FIELDS
            },
        )
        for row in original.scopes.all()
    ])
    HealthAffectedStock.objects.bulk_create([
        HealthAffectedStock(
            observation=replacement, plant_id=row.plant_id,
            cohort_id=row.cohort_id, quantity=row.quantity,
        )
        for row in original.affected_stock.all()
    ])
    HealthObservationDiagnosis.objects.bulk_create([
        HealthObservationDiagnosis(
            observation=replacement, diagnosis=diagnosis, certainty=certainty,
        )
        for diagnosis, certainty in diagnoses
    ])
    HealthEvidenceLink.objects.bulk_create([
        HealthEvidenceLink(observation=replacement, **item) for item in evidence
    ])
    return replacement
