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
from .growth import current_growth, record_observation
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


def _existing(workspace, idempotency_key, action, payload, loss_cause=None):
    """Return the operation this key already recorded, if it is the same work.

    The loss cause is compared against the stored column rather than copied into
    the payload: it is already an immutable part of the recorded fact, and a
    replay carrying a different cause is a different loss whatever the payload
    says.
    """
    operation = CohortOperation.objects.filter(
        workspace=workspace,
        idempotency_key=idempotency_key,
    ).first()
    if operation is None:
        return None
    recorded_request = {key: operation.payload.get(key) for key in payload}
    same_cause = loss_cause is None or operation.loss_cause == loss_cause
    if operation.action != action or recorded_request != payload or not same_cause:
        raise ValidationError({'idempotency_key': 'This key was already used for different work.'})
    return operation


def _operation(workspace, user, action, idempotency_key, occurred_at, reason, payload, loss_cause=''):
    return CohortOperation.objects.create(
        workspace=workspace,
        created_by=_actor(user),
        action=action,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at or timezone.now(),
        reason=reason,
        loss_cause=loss_cause,
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


def _locked(cohort_id, workspace):
    return PlantCohort.objects.select_for_update().get(pk=cohort_id, workspace=workspace)


def lock_cohorts(workspace, cohort_ids):
    """Lock exact workspace cohorts in deterministic primary-key order.

    Deterministic order for the same reason `inventory.ledger.lock_lots` takes
    it: two orders drawing on the same two blocks have to queue rather than
    deadlock against each other.
    """
    requested = sorted(set(cohort_ids))
    cohorts = list(
        PlantCohort.objects.select_for_update(of=('self',))
        .select_related('batch')
        .filter(workspace=workspace, pk__in=requested)
        .order_by('pk')
    )
    if len(cohorts) != len(requested):
        raise ValidationError({'cohorts': 'One or more cohorts are unavailable.'})
    return {cohort.pk: cohort for cohort in cohorts}


def _lock(cohort_id, workspace, expected_revision):
    cohort = _locked(cohort_id, workspace)
    if cohort.revision != expected_revision:
        raise ValidationError({'revision': 'The cohort changed after it was loaded.'})
    return cohort


def _require_not_quarantined(cohort):
    """Keep structural and commercial changes behind the health workflow."""
    from health.availability import is_quarantined  # pylint: disable=import-outside-toplevel

    if is_quarantined(cohort):
        raise ValidationError({'cohort': 'Release this cohort from quarantine first.'})


def _save(cohort):
    cohort.revision += 1
    cohort.full_clean()
    cohort.save(update_fields=['quantity', 'lifecycle_state', 'location', 'revision', 'updated'])


def _observation_values(growth, container_count=None):
    """Copy effective homogeneous facts onto a structurally changed identity."""
    values = {
        field: growth[field]
        for field in (
            'stage', 'grade', 'height_cm', 'spread_cm', 'root_condition',
            'expected_ready', 'photo_url',
        )
        if growth[field] not in (None, '')
    }
    if growth['container_item'] is not None and container_count is not None:
        values.update({
            'container_item': growth['container_item'],
            'container_count': container_count,
        })
    return values


def _container_allocation(growth, allocated, remaining_quantity):
    """Validate an explicit whole-container allocation for a quantity change."""
    current = growth['container_count']
    if current is None:
        if allocated is not None:
            raise ValidationError({'container_count': 'This cohort has no container assignment.'})
        return None, None
    if allocated is None:
        raise ValidationError({'container_count': 'Allocate containers explicitly for this operation.'})
    remaining = current - allocated
    if allocated <= 0 or remaining < 0 or (remaining_quantity > 0 and remaining == 0):
        raise ValidationError({'container_count': 'The container allocation is not physically possible.'})
    return allocated, remaining


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
                  location=None, loss_cause=None, payload_extra=None,
                  allow_quarantined=False):
    """Apply an adjustment, loss, lifecycle change, or whole-cohort move."""
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
        'location': location.pk if location else None,
        **(payload_extra or {}),
    }
    existing = _existing(workspace, idempotency_key, action, payload, loss_cause)
    if existing:
        return existing.events.get().cohort, existing
    cohort = _lock(cohort_id, workspace, expected_revision)
    if not allow_quarantined:
        _require_not_quarantined(cohort)
    before = _snapshot(cohort)
    if action == CohortOperation.Action.ADJUST:
        _require_reason(reason)
        if quantity is None or quantity < 0:
            raise ValidationError({'quantity': 'Counted quantity must be zero or greater.'})
        cohort.quantity = quantity
    elif action == CohortOperation.Action.LOSS:
        _require_reason(reason)
        if not loss_cause:
            raise ValidationError({'loss_cause': 'A loss needs a recorded cause.'})
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
    operation = _operation(
        workspace, user, action, idempotency_key, occurred_at, reason, payload,
        loss_cause or '',
    )
    _event(operation, cohort, before)
    _reallocate(cohort.batch, user, reason or operation.get_action_display())
    return cohort, operation


@transaction.atomic
def split_cohort(workspace, user, *, cohort_id, expected_revision, quantity,
                 idempotency_key, occurred_at=None, reason='', location=None,
                 container_count=None):
    """Move part of a cohort into a new identity, optionally at a new location."""
    _require_reason(reason)
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
        'location': location.pk if location else None,
        'container_count': container_count,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.SPLIT, payload)
    if existing:
        return existing.events.order_by('pk').last().cohort, existing
    source = _lock(cohort_id, workspace, expected_revision)
    _require_not_quarantined(source)
    if quantity <= 0 or quantity >= source.quantity:
        raise ValidationError({'quantity': 'Split quantity must be less than the cohort quantity.'})
    destination = location or source.location
    growth = current_growth(source)
    allocated, remaining = _container_allocation(
        growth, container_count, source.quantity - quantity,
    )
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
    occurred = occurred_at or timezone.now()
    child_values = _observation_values(growth, allocated)
    if child_values:
        record_observation(workspace, user, cohort_id=child.pk, occurred_at=occurred, **child_values)
    if remaining is not None:
        record_observation(
            workspace, user, cohort_id=source.pk, occurred_at=occurred,
            container_item=growth['container_item'], container_count=remaining,
            notes=reason,
        )
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
        _require_not_quarantined(cohort)
    for cohort in cohorts:
        if cohort.revision != int(revisions.get(str(cohort.pk), revisions.get(cohort.pk, -1))):
            raise ValidationError({'revision': f'Cohort {cohort.pk} changed after it was loaded.'})
    target = by_id[target_id]
    target_growth = current_growth(target)
    signature = (
        target.batch_id, target.source_sowing_id, target.lifecycle_state, target.location_id,
        target_growth['stage'].pk if target_growth['stage'] else None,
        target_growth['grade'].pk if target_growth['grade'] else None,
        target_growth['container_item'].pk if target_growth['container_item'] else None,
        target_growth['expected_ready'],
    )

    def incompatible(row):
        growth = current_growth(row)
        row_signature = (
            row.batch_id, row.source_sowing_id, row.lifecycle_state, row.location_id,
            growth['stage'].pk if growth['stage'] else None,
            growth['grade'].pk if growth['grade'] else None,
            growth['container_item'].pk if growth['container_item'] else None,
            growth['expected_ready'],
        )
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
    if target_growth['container_item'] is not None:
        total_containers = sum(current_growth(row)['container_count'] for row in cohorts)
        record_observation(
            workspace, user, cohort_id=target.pk,
            occurred_at=occurred_at or timezone.now(),
            container_item=target_growth['container_item'],
            container_count=total_containers,
            notes=reason,
        )
    _reallocate(target.batch, user, reason)
    return target, operation


@transaction.atomic
def promote_cohort(workspace, user, *, cohort_id, expected_revision, quantity,
                   idempotency_key, occurred_at=None, reason='', container_count=None):
    """Replace an anonymous quantity with the same number of concrete plant IDs."""
    _require_reason(reason)
    payload = {
        'cohort': cohort_id,
        'expected_revision': expected_revision,
        'quantity': quantity,
        'container_count': container_count,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.PROMOTE, payload)
    if existing:
        return list(SpecificPlant.objects.filter(pk__in=existing.payload['plants'])), existing
    cohort = _lock(cohort_id, workspace, expected_revision)
    _require_not_quarantined(cohort)
    if quantity <= 0 or quantity > cohort.quantity:
        raise ValidationError({'quantity': 'Promotion must be within the current quantity.'})
    before = _snapshot(cohort)
    growth = current_growth(cohort)
    allocated, remaining = _container_allocation(
        growth, container_count, cohort.quantity - quantity,
    )
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
    plant_values = _observation_values(growth, allocated)
    if plant_values:
        record_observation(
            workspace, user, plant_ids=[plant.pk for plant in plants],
            occurred_at=promoted_at, **plant_values,
        )
    if remaining is not None and cohort.quantity > 0:
        record_observation(
            workspace, user, cohort_id=cohort.pk, occurred_at=promoted_at,
            container_item=growth['container_item'], container_count=remaining,
            notes=reason,
        )
    operation.payload = {**payload, 'plants': [plant.pk for plant in plants]}
    CohortOperation.objects.filter(pk=operation.pk).update(payload=operation.payload)
    _reallocate(cohort.batch, user, reason)
    return plants, operation


@transaction.atomic
def sell_cohort(workspace, user, *, cohort_id, quantity, idempotency_key,
                occurred_at=None, reason='', reference='', require_available=True):
    """Take a sold quantity out of a cohort without naming what left.

    No revision is checked here. The optimistic-concurrency question — is this
    still the block the operator was looking at? — belongs to the moment a
    quantity is reserved against a number somebody read; by dispatch the
    promise already exists, and what has to hold is that the block still holds
    the count and is still fit to sell. The row lock is what makes that true.

    `require_available` is dropped only when a return is being undone: those
    units are going back to a customer who already had them rather than being
    newly offered, so the block they are leaving need not be on sale, and a
    quarantine opened by the return itself must not trap them.

    The caller reallocates the batch afterwards rather than this doing it. A
    sold quantity stays an output of its batch — see `costing.sources` — and
    which units count as sold is read from the order allocation's own status,
    which the caller settles in this same transaction.
    """
    payload = {'cohort': cohort_id, 'quantity': quantity, 'reference': reference}
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.SOLD, payload)
    if existing:
        return existing.events.get()
    cohort = _locked(cohort_id, workspace)
    if require_available:
        _require_not_quarantined(cohort)
        if cohort.lifecycle_state != PlantCohort.LifecycleState.AVAILABLE:
            raise ValidationError({'cohort': 'Only an available cohort can be sold.'})
    if quantity <= 0 or quantity > cohort.quantity:
        raise ValidationError({'quantity': 'A sale must be within the current quantity.'})
    before = _snapshot(cohort)
    cohort.quantity -= quantity
    if cohort.quantity == 0:
        cohort.lifecycle_state = PlantCohort.LifecycleState.DEPLETED
    _save(cohort)
    operation = _operation(
        workspace, user, CohortOperation.Action.SOLD,
        idempotency_key, occurred_at, reason, payload,
    )
    return _event(operation, cohort, before)


@transaction.atomic
def return_cohort(workspace, user, *, source_cohort_id, quantity, idempotency_key,
                  into_source=False, location=None, state=None, occurred_at=None,
                  reason='', reference=''):
    """Bring a quantity back from an order into anonymous stock.

    A customer return lands in a new block linked to the one it left, because
    stock that has been to a customer and back is not the same fact as stock
    that never went. It may come back fit only for quarantine or for the skip,
    and the block it left may since have been split, moved, promoted or sold
    out entirely; imposing the returned units' condition on stock that never
    moved would be a claim nobody made, and merging into a block that no longer
    describes them would lose the distinction. `CohortEvent.source_cohorts`
    keeps the ancestry, so the returned count is still traceable to its batch,
    sowing and original block.

    `into_source` is the one case where the count goes back exactly where it
    was: reversing a dispatch says the dispatch never happened, so the block
    has to end up as it would have been, `state` included.

    As with `sell_cohort`, the caller reallocates the batch once the order's
    own facts are settled.
    """
    payload = {
        'source': source_cohort_id,
        'quantity': quantity,
        'into_source': into_source,
        'location': location.pk if location else None,
        'reference': reference,
    }
    existing = _existing(workspace, idempotency_key, CohortOperation.Action.RETURN, payload)
    if existing:
        return existing.events.get()
    if quantity <= 0:
        raise ValidationError({'quantity': 'A return must be a quantity of at least one.'})
    source = _locked(source_cohort_id, workspace)
    occurred = occurred_at or timezone.now()
    if into_source:
        return _restore_into(workspace, user, source, quantity, state, idempotency_key, occurred, reason, payload)
    return _open_returned(workspace, user, source, quantity, location, idempotency_key, occurred, reason, payload)


def _restore_into(workspace, user, cohort, quantity, state, idempotency_key, occurred_at, reason, payload):
    """Put a dispatched count back into the block it left, exactly as it was."""
    before = _snapshot(cohort)
    cohort.quantity += quantity
    cohort.lifecycle_state = state or (
        PlantCohort.LifecycleState.AVAILABLE
        if before['state'] == PlantCohort.LifecycleState.DEPLETED
        else before['state']
    )
    _save(cohort)
    operation = _operation(
        workspace, user, CohortOperation.Action.RETURN,
        idempotency_key, occurred_at, reason, payload,
    )
    return _event(operation, cohort, before)


def _open_returned(workspace, user, source, quantity, location, idempotency_key, occurred_at, reason, payload):
    """Open a new block for a customer return, carrying the source's lineage."""
    if location is not None:
        check_capacity(location, cohort_contribution(quantity))
    growth = current_growth(source)
    returned = PlantCohort.objects.create(
        workspace=workspace,
        batch=source.batch,
        source_sowing=source.source_sowing,
        quantity=quantity,
        lifecycle_state=PlantCohort.LifecycleState.AVAILABLE,
        location=location,
        observed_at=source.observed_at,
        notes=reason,
        created_by=_actor(user),
    )
    operation = _operation(
        workspace, user, CohortOperation.Action.RETURN,
        idempotency_key, occurred_at, reason, payload,
    )
    event = _event(operation, returned, {
        'quantity': 0,
        'state': returned.lifecycle_state,
        'location': None,
    }, sources=(source,))
    # The container count is deliberately not carried over: what came back is
    # a count of plants, and how many trays or pots they arrived in is a fact
    # only the person unpacking them can record.
    returned_values = _observation_values(growth)
    if returned_values:
        record_observation(workspace, user, cohort_id=returned.pk, occurred_at=occurred_at, **returned_values)
    return event
