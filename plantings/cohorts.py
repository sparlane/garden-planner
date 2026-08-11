"""Transactional commands for quantity-based nursery plant cohorts."""

# Cohort commands deliberately expose the complete audited request at their
# boundary; grouping those values into untyped dictionaries would obscure the
# contract and merely move the same branching and local state elsewhere.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from locations.occupancy import check_capacity, cohort_contribution

from .lifecycle import EventType, OutcomeRequest, record_germination_event, record_lifecycle_event
from .models import (
    CohortEvent,
    CohortOperation,
    PlantCohort,
    ProductionBatch,
    SpecificPlant,
    SpecificPlantLocation,
)


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _require_reason(reason):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def _existing(workspace, idempotency_key, action, payload):
    operation = CohortOperation.objects.filter(
        workspace=workspace,
        idempotency_key=idempotency_key,
    ).first()
    recorded_request = {
        key: operation.payload.get(key)
        for key in payload
    } if operation else None
    if operation and (operation.action != action or recorded_request != payload):
        raise ValidationError({'idempotency_key': 'This key was already used for different work.'})
    return operation


def _operation(workspace, user, action, idempotency_key, occurred_at, reason, payload):
    return CohortOperation.objects.create(
        workspace=workspace,
        created_by=_actor(user),
        action=action,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at or timezone.now(),
        reason=reason,
        payload=payload,
    )


def _event(operation, cohort, before, sources=()):
    event = CohortEvent.objects.create(
        workspace=cohort.workspace,
        operation=operation,
        cohort=cohort,
        quantity_before=before['quantity'],
        quantity_delta=cohort.quantity - before['quantity'],
        quantity_after=cohort.quantity,
        state_before=before['state'],
        state_after=cohort.lifecycle_state,
        location_before_id=before['location'],
        location_after=cohort.location,
    )
    if sources:
        event.source_cohorts.add(*sources)
    return event


def _snapshot(cohort):
    return {
        'quantity': cohort.quantity,
        'state': cohort.lifecycle_state,
        'location': cohort.location_id,
    }


def _lock(cohort_id, workspace, expected_revision):
    cohort = PlantCohort.objects.select_for_update().get(pk=cohort_id, workspace=workspace)
    if cohort.revision != expected_revision:
        raise ValidationError({'revision': 'The cohort changed after it was loaded.'})
    return cohort


def _save(cohort):
    cohort.revision += 1
    cohort.full_clean()
    cohort.save(update_fields=['quantity', 'lifecycle_state', 'location', 'revision', 'updated'])


def _reallocate(batch, user, reason):
    """Keep the append-only cost layers aligned with the changed output units."""
    from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

    reallocate_batch(batch, user, 'manual_recalculate', reason)


@transaction.atomic
def observe_cohort(workspace, user, *, batch, quantity, idempotency_key,
                   source_sowing=None, location=None, occurred_at=None, notes=''):
    """Record one initial homogeneous quantity and its optional sowing lineage."""
    payload = {
        'batch': batch.pk,
        'source_sowing': source_sowing.pk if source_sowing else None,
        'quantity': quantity,
        'location': location.pk if location else None,
        'notes': notes,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.OBSERVE, payload)
    if existing:
        return existing.events.get().cohort, existing
    if quantity <= 0:
        raise ValidationError({'quantity': 'Quantity must be greater than zero.'})
    batch = ProductionBatch.objects.select_for_update().get(pk=batch.pk, workspace=workspace)
    if source_sowing and (source_sowing.workspace_id != workspace.pk or source_sowing.batch_id != batch.pk):
        raise ValidationError({'source_sowing': 'The sowing is not part of this batch.'})
    if location:
        check_capacity(location, cohort_contribution(quantity))
    cohort = PlantCohort.objects.create(
        workspace=workspace,
        batch=batch,
        source_sowing=source_sowing,
        quantity=quantity,
        location=location,
        observed_at=occurred_at or timezone.now(),
        notes=notes,
        created_by=_actor(user),
    )
    operation = _operation(
        workspace, user, CohortOperation.Action.OBSERVE,
        idempotency_key, occurred_at, '', payload,
    )
    _event(operation, cohort, {
        'quantity': 0,
        'state': PlantCohort.LifecycleState.GROWING,
        'location': None,
    })
    _reallocate(batch, user, 'Cohort observed.')
    return cohort, operation


@transaction.atomic
def change_cohort(workspace, user, *, cohort_id, expected_revision, action,
                  idempotency_key, occurred_at=None, reason='', quantity=None,
                  location=None, payload_extra=None):
    """Apply an adjustment, loss, lifecycle change, or whole-cohort move."""
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
        'location': location.pk if location else None,
        **(payload_extra or {}),
    }
    existing = _existing(workspace, idempotency_key, action, payload)
    if existing:
        return existing.events.get().cohort, existing
    cohort = _lock(cohort_id, workspace, expected_revision)
    before = _snapshot(cohort)
    if action == CohortOperation.Action.ADJUST:
        _require_reason(reason)
        if quantity is None or quantity < 0:
            raise ValidationError({'quantity': 'Counted quantity must be zero or greater.'})
        cohort.quantity = quantity
    elif action == CohortOperation.Action.LOSS:
        _require_reason(reason)
        if quantity is None or quantity <= 0 or quantity > cohort.quantity:
            raise ValidationError({'quantity': 'Loss must be within the current quantity.'})
        cohort.quantity -= quantity
    elif action == CohortOperation.Action.MOVE:
        if location is None:
            raise ValidationError({'location': 'A destination is required.'})
        check_capacity(location, cohort_contribution(cohort.quantity, cohort))
        cohort.location = location
    elif action == CohortOperation.Action.READY:
        if cohort.lifecycle_state != PlantCohort.LifecycleState.GROWING:
            raise ValidationError({'action': 'Only a growing cohort can be made available.'})
        cohort.lifecycle_state = PlantCohort.LifecycleState.AVAILABLE
    elif action == CohortOperation.Action.RETAIN:
        retainable = (
            PlantCohort.LifecycleState.GROWING,
            PlantCohort.LifecycleState.AVAILABLE,
        )
        if cohort.lifecycle_state not in retainable:
            raise ValidationError({'action': 'This cohort cannot be retained.'})
        cohort.lifecycle_state = PlantCohort.LifecycleState.RETAINED
    else:
        raise ValidationError({'action': 'Select a supported cohort action.'})
    if cohort.quantity == 0:
        cohort.lifecycle_state = PlantCohort.LifecycleState.DEPLETED
    _save(cohort)
    operation = _operation(workspace, user, action, idempotency_key, occurred_at, reason, payload)
    _event(operation, cohort, before)
    _reallocate(cohort.batch, user, reason or operation.get_action_display())
    return cohort, operation


@transaction.atomic
def split_cohort(workspace, user, *, cohort_id, expected_revision, quantity,
                 idempotency_key, occurred_at=None, reason='', location=None):
    """Move part of a cohort into a new identity, optionally at a new location."""
    _require_reason(reason)
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
        'location': location.pk if location else None,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.SPLIT, payload)
    if existing:
        return existing.events.order_by('pk').last().cohort, existing
    source = _lock(cohort_id, workspace, expected_revision)
    if quantity <= 0 or quantity >= source.quantity:
        raise ValidationError({'quantity': 'Split quantity must be less than the cohort quantity.'})
    destination = location or source.location
    if destination != source.location:
        check_capacity(destination, cohort_contribution(quantity))
    source_before = _snapshot(source)
    source.quantity -= quantity
    _save(source)
    child = PlantCohort.objects.create(
        workspace=workspace,
        batch=source.batch,
        source_sowing=source.source_sowing,
        quantity=quantity,
        lifecycle_state=source.lifecycle_state,
        location=destination,
        observed_at=source.observed_at,
        notes=source.notes,
        created_by=_actor(user),
    )
    operation = _operation(
        workspace, user, CohortOperation.Action.SPLIT,
        idempotency_key, occurred_at, reason, payload,
    )
    _event(operation, source, source_before)
    _event(operation, child, {
        'quantity': 0,
        'state': child.lifecycle_state,
        'location': None,
    }, sources=(source,))
    _reallocate(source.batch, user, reason)
    return child, operation


@transaction.atomic
def merge_cohorts(workspace, user, *, target_id, revisions, source_ids,
                  idempotency_key, occurred_at=None, reason=''):
    """Fold compatible sources into an existing target without losing ancestry."""
    _require_reason(reason)
    ids = sorted(set(source_ids) | {target_id})
    payload = {'target': target_id, 'sources': sorted(set(source_ids)), 'revisions': revisions}
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.MERGE, payload)
    if existing:
        return existing.events.get(cohort_id=target_id).cohort, existing
    cohorts = list(
        PlantCohort.objects.select_for_update().filter(workspace=workspace, pk__in=ids).order_by('pk')
    )
    if len(cohorts) != len(ids) or target_id in source_ids:
        raise ValidationError({'cohorts': 'Select one target and distinct source cohorts.'})
    by_id = {cohort.pk: cohort for cohort in cohorts}
    for cohort in cohorts:
        if cohort.revision != int(revisions.get(str(cohort.pk), revisions.get(cohort.pk, -1))):
            raise ValidationError({'revision': f'Cohort {cohort.pk} changed after it was loaded.'})
    target = by_id[target_id]
    signature = (target.batch_id, target.source_sowing_id, target.lifecycle_state, target.location_id)

    def incompatible(row):
        row_signature = (row.batch_id, row.source_sowing_id, row.lifecycle_state, row.location_id)
        return row_signature != signature or row.quantity == 0

    if any(incompatible(row) for row in cohorts):
        raise ValidationError({'cohorts': 'Cohorts must share batch, lineage, state, and location.'})
    snapshots = {row.pk: _snapshot(row) for row in cohorts}
    target.quantity += sum(by_id[source_id].quantity for source_id in source_ids)
    _save(target)
    for source_id in source_ids:
        source = by_id[source_id]
        source.quantity = 0
        source.lifecycle_state = PlantCohort.LifecycleState.DEPLETED
        _save(source)
    operation = _operation(
        workspace, user, CohortOperation.Action.MERGE,
        idempotency_key, occurred_at, reason, payload,
    )
    sources = [by_id[source_id] for source_id in source_ids]
    _event(operation, target, snapshots[target.pk], sources=sources)
    for source in sources:
        _event(operation, source, snapshots[source.pk])
    _reallocate(target.batch, user, reason)
    return target, operation


@transaction.atomic
def promote_cohort(workspace, user, *, cohort_id, expected_revision, quantity,
                   idempotency_key, occurred_at=None, reason=''):
    """Replace an anonymous quantity with the same number of concrete plant IDs."""
    _require_reason(reason)
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.PROMOTE, payload)
    if existing:
        return list(SpecificPlant.objects.filter(pk__in=existing.payload['plants'])), existing
    cohort = _lock(cohort_id, workspace, expected_revision)
    if quantity <= 0 or quantity > cohort.quantity:
        raise ValidationError({'quantity': 'Promotion must be within the current quantity.'})
    before = _snapshot(cohort)
    promoted_at = occurred_at or timezone.now()
    plants = []
    for _index in range(quantity):
        plant = SpecificPlant.objects.create(
            workspace=workspace,
            batch=cohort.batch,
            promoted_from_cohort=cohort,
            germinated=cohort.observed_at,
        )
        record_germination_event(plant, user)
        if cohort.lifecycle_state == PlantCohort.LifecycleState.AVAILABLE:
            record_lifecycle_event(
                plant, user,
                OutcomeRequest(EventType.READY, occurred_at=promoted_at, reason=reason),
            )
        if cohort.location_id:
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.LOCATION,
                location=cohort.location,
                started=promoted_at,
                notes=f'Promoted from cohort {cohort.pk}.',
            )
        plants.append(plant)
    cohort.quantity -= quantity
    if cohort.quantity == 0:
        cohort.lifecycle_state = PlantCohort.LifecycleState.DEPLETED
    _save(cohort)
    operation = _operation(
        workspace, user, CohortOperation.Action.PROMOTE,
        idempotency_key, promoted_at, reason, payload,
    )
    _event(operation, cohort, before)
    operation.payload = {**payload, 'plants': [plant.pk for plant in plants]}
    CohortOperation.objects.filter(pk=operation.pk).update(payload=operation.payload)
    _reallocate(cohort.batch, user, reason)
    return plants, operation
