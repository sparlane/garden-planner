"""Reviewed, idempotent bulk work over individually identified plants."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from locations.models import Location
from locations.occupancy import capacity_chain, location_occupancy

from .batches import lock_batch_with_plants
from .lifecycle import (
    STATE_AFTER,
    EventType,
    OutcomeRequest,
    is_final,
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
    validate_outcome,
)
from .models import (
    BulkPlantOperation,
    BulkPlantOperationResult,
    SeedTrayCellPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from .growth import record_observation


ACTION_EVENTS = {
    BulkPlantOperation.Action.READY: EventType.READY,
    BulkPlantOperation.Action.RETAIN: EventType.RETAINED,
    BulkPlantOperation.Action.DONATE: EventType.DONATED,
    BulkPlantOperation.Action.FAIL: EventType.FAILED,
    BulkPlantOperation.Action.CULL: EventType.CULLED,
    BulkPlantOperation.Action.FINISH_HARVEST: EventType.HARVEST_FINISHED,
    BulkPlantOperation.Action.HOLD_BACK: EventType.HELD_BACK,
    BulkPlantOperation.Action.END_RETENTION: EventType.RETENTION_ENDED,
}


@dataclass(frozen=True)
# pylint: disable-next=too-many-instance-attributes
class BulkOperationRequest:
    """One validated preview or confirmed bulk-operation request."""

    action: str
    atomicity: str
    occurred_at: object
    reason: str
    plants: tuple = ()
    selection_source: dict = None
    action_payload: dict = None
    idempotency_key: object = None


class BulkOperationConflict(Exception):
    """A confirmed plan had no permitted set of writes."""

    def __init__(self, preview):
        super().__init__('The bulk operation has conflicts.')
        self.preview = preview


def _errors(error):
    """Flatten a domain validation error into operator-readable messages."""
    if hasattr(error, 'message_dict'):
        return [str(message) for messages in error.message_dict.values() for message in messages]
    return [str(message) for message in error.messages]


def _json_value(value):
    """Turn validated model/date values into a stable JSON audit value."""
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(entry) for key, entry in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(entry) for entry in value]
    return value


def request_digest(request):
    """Hash every part of a request whose reuse must mean the same work."""
    content = {
        'action': request.action,
        'atomicity': request.atomicity,
        'occurred_at': _json_value(request.occurred_at),
        'reason': request.reason,
        'plants': sorted(set(request.plants)),
        'selection_source': _json_value(request.selection_source or {}),
        'action_payload': _json_value(request.action_payload or {}),
    }
    encoded = json.dumps(content, sort_keys=True, separators=(',', ':')).encode()
    return sha256(encoded).hexdigest()


def _plant_conflicts(plant, request):
    """Return conflicts independent of aggregate destination capacity."""
    try:
        if request.action in ACTION_EVENTS:
            validate_outcome(
                plant,
                ACTION_EVENTS[request.action],
                request.occurred_at,
                request.reason,
            )
        elif request.action == BulkPlantOperation.Action.MOVE:
            active = (
                SpecificPlantLocation.objects
                .filter(specific_plant=plant, ended__isnull=True)
                .first()
            )
            if active is not None and request.occurred_at < active.started:
                raise ValidationError({'occurred_at': 'Move cannot start before the active location.'})
            if request.action_payload['location_type'] == SpecificPlantLocation.GARDEN_SQUARE:
                validate_outcome(plant, EventType.TRANSPLANTED, request.occurred_at)
        elif request.action in {
            BulkPlantOperation.Action.STAGE,
            BulkPlantOperation.Action.GRADE,
            BulkPlantOperation.Action.REPOT,
        } and is_final(plant_lifecycle_summary(plant).state):
            raise ValidationError({'plants': 'Finished plants cannot receive nursery observations.'})
    except ValidationError as exc:
        return _errors(exc)
    return []


def _lock_capacity_chain(destination):
    """Lock capacity rows in the same deterministic order as single moves."""
    limits = capacity_chain(destination)
    if not limits:
        return []
    return list(
        Location.objects
        .select_for_update()
        .filter(pk__in=[limit.pk for limit in limits])
        .order_by('pk')
    )


def _capacity_slots(destination, override_reason, lock):
    """Return how many directly placed plants fit and any category conflict."""
    if not destination.active:
        return 0, ['The destination location is inactive.'], []
    limits = _lock_capacity_chain(destination) if lock else capacity_chain(destination)
    slots = None
    capacity = []
    for limit in limits:
        basis = limit.capacity_basis
        if basis not in Location.ENFORCED_BASES:
            continue
        if basis not in {Location.CapacityBasis.PLANTS, Location.CapacityBasis.CONTAINERS}:
            if limit.pk == destination.pk:
                return 0, [f'{limit.name} is measured in {basis}, which a plant does not occupy.'], capacity
            continue
        used = location_occupancy(limit, subtree=True).of(basis)
        remaining = limit.capacity_value - used
        available = max(0, int(remaining.to_integral_value(rounding=ROUND_FLOOR)))
        capacity.append({
            'location': limit.pk,
            'basis': basis,
            'capacity': str(limit.capacity_value),
            'used': used,
            'available': available,
        })
        if not override_reason:
            slots = available if slots is None else min(slots, available)
    return slots, [], capacity


def _load_plants(workspace, plant_ids, lock):
    """Resolve an explicit selection without admitting another workspace."""
    wanted = sorted(set(plant_ids))
    if not wanted:
        raise ValidationError({'plants': 'Select at least one plant.'})
    queryset = SpecificPlant.objects.filter(workspace=workspace, pk__in=wanted).order_by('pk')
    if lock:
        queryset = queryset.select_for_update()
    plants = list(queryset)
    known = {plant.pk for plant in plants}
    missing = [plant_id for plant_id in wanted if plant_id not in known]
    if missing:
        raise ValidationError({'plants': f'No such plants in this workspace: {missing}.'})
    return plants


def _projected_state(request):
    """Return the state an eligible lifecycle action leaves behind.

    Read from `STATE_AFTER` rather than repeated here, so the preview cannot
    describe a different outcome from the one the write actually derives.
    """
    return STATE_AFTER.get(ACTION_EVENTS.get(request.action))


def _plant_preview(workspace, request, lock=False):  # pylint: disable=too-many-locals
    """Plan one action over concrete plants, optionally under execution locks."""
    plants = _load_plants(workspace, request.plants, lock)
    capacity = []
    slots = None
    capacity_error = []
    moves_to_location = False
    if request.action == BulkPlantOperation.Action.MOVE:
        moves_to_location = (
            request.action_payload['location_type'] == SpecificPlantLocation.LOCATION
        )
    if moves_to_location:
        slots, capacity_error, capacity = _capacity_slots(
            request.action_payload['location'],
            request.action_payload.get('override_reason', ''),
            lock,
        )

    rows = []
    capacity_used = 0
    for plant in plants:
        summary = plant_lifecycle_summary(plant)
        conflicts = _plant_conflicts(plant, request)
        if not conflicts and capacity_error:
            conflicts = list(capacity_error)
        if not conflicts and slots is not None:
            if capacity_used >= slots:
                conflicts = ['The destination does not have capacity for this plant.']
            else:
                capacity_used += 1
        after_state = _projected_state(request) or summary.state
        rows.append({
            'plant': plant.pk,
            'eligible': not conflicts,
            'conflicts': conflicts,
            'before': {'lifecycle_state': summary.state},
            'after': {
                'lifecycle_state': after_state,
                'location_type': (
                    request.action_payload.get('location_type')
                    if request.action == BulkPlantOperation.Action.MOVE else None
                ),
            },
        })
    eligible = sum(row['eligible'] for row in rows)
    preview = {
        'action': request.action,
        'selected': len(rows),
        'eligible': eligible,
        'conflicts': len(rows) - eligible,
        'plants': rows,
        'capacity': capacity,
    }
    if request.action == BulkPlantOperation.Action.REPOT:
        preview['application'] = _preview_repot_application(workspace, plants, request)
    return preview


def _application_request(workspace, plants, values):
    """Build an application whose targets are the reviewed concrete plants."""
    from applications.models import InputApplicationTarget  # pylint: disable=import-outside-toplevel
    from applications.rest import _build_request  # pylint: disable=import-outside-toplevel,protected-access
    from applications.services import TargetRequest  # pylint: disable=import-outside-toplevel

    request = _build_request(workspace, values)
    targets = tuple(
        TargetRequest(
            target_type=InputApplicationTarget.TargetType.SPECIFIC_PLANT,
            target=plant,
        )
        for plant in plants
    )
    return request._replace(lines=tuple(
        line._replace(targets=targets) for line in request.lines
    ))


def _preview_repot_application(workspace, plants, request):
    """Use the posting service's calculations but roll its draft back."""
    from applications.services import application_state, create_application_draft  # pylint: disable=import-outside-toplevel

    with transaction.atomic():
        draft = create_application_draft(
            workspace, None,
            _application_request(workspace, plants, request.action_payload['application']),
        )
        state = application_state(draft)
        transaction.set_rollback(True)
    return state


def _germination_preview(workspace, request, lock=False):
    """Validate every source allocation for a quantity germination."""
    requested = request.action_payload['germinations']
    requested_ids = [entry['cell_planting'].pk for entry in requested]
    queryset = SeedTrayCellPlanting.objects.filter(
        pk__in=requested_ids,
        seed_tray_planting__workspace=workspace,
    )
    if lock:
        queryset = queryset.select_for_update()
    allocations = list(
        queryset.select_related('seed_tray_planting__batch').order_by('pk')
    )
    available_ids = {allocation.pk for allocation in allocations}
    conflicts = []
    if any(entry['cell_planting'].pk not in available_ids for entry in requested):
        conflicts.append('The cell allocation is unavailable in this workspace.')
    selected = sum(entry['quantity'] for entry in requested)
    quantities = {entry['quantity'] for entry in requested}
    return {
        'action': request.action,
        'selected': selected,
        'eligible': 0 if conflicts else selected,
        'conflicts': len(conflicts),
        'plants': [],
        'capacity': [],
        'source': {
            'cell_plantings': requested_ids,
            'quantity': quantities.pop() if len(quantities) == 1 else None,
            'germinations': [
                {
                    'cell_planting': entry['cell_planting'].pk,
                    'quantity': entry['quantity'],
                }
                for entry in requested
            ],
            'conflicts': conflicts,
        },
    }


def preview_bulk_operation(workspace, request, lock=False):
    """Return eligibility and projected effects without changing a plant."""
    if request.action == BulkPlantOperation.Action.GERMINATE:
        return _germination_preview(workspace, request, lock)
    return _plant_preview(workspace, request, lock)


def _create_operation(workspace, user, request, digest):
    """Claim the idempotency key before any domain writes occur."""
    return BulkPlantOperation.objects.create(
        workspace=workspace,
        idempotency_key=request.idempotency_key,
        request_digest=digest,
        action=request.action,
        atomicity=request.atomicity,
        occurred_at=request.occurred_at,
        reason=request.reason,
        selection_source=_json_value(request.selection_source or {}),
        action_payload=_json_value(request.action_payload or {}),
        created_by=user if user is not None and user.is_authenticated else None,
    )


def _move_data(request):
    """Build the existing single-plant move service payload."""
    return {
        **request.action_payload,
        'started': request.occurred_at,
        'notes': request.reason,
    }


def _apply_plant_operation(operation, user, request, preview):
    """Apply eligible plan rows and append one result for every selection member."""
    from .rest import move_specific_plant  # pylint: disable=import-outside-toplevel

    rows = {row['plant']: row for row in preview['plants']}
    plants = {
        plant.pk: plant
        for plant in SpecificPlant.objects.filter(pk__in=rows).order_by('pk')
    }
    eligible_plants = [plants[plant_id] for plant_id, row in rows.items() if row['eligible']]
    observation = None
    if request.action in {
        BulkPlantOperation.Action.STAGE,
        BulkPlantOperation.Action.GRADE,
        BulkPlantOperation.Action.REPOT,
    }:
        values = {
            'occurred_at': request.occurred_at,
            'notes': request.action_payload.get('notes', '') or request.reason,
        }
        if request.action == BulkPlantOperation.Action.STAGE:
            values['stage'] = request.action_payload['stage']
        elif request.action == BulkPlantOperation.Action.GRADE:
            values['grade'] = request.action_payload['grade']
        else:
            values.update(_post_repot_application(
                operation.workspace, user, eligible_plants, request,
            ))
        observation = record_observation(
            operation.workspace, user,
            plant_ids=[plant.pk for plant in eligible_plants],
            **values,
        )
    for plant_id, row in rows.items():
        plant = plants[plant_id]
        if not row['eligible']:
            BulkPlantOperationResult.objects.create(
                workspace=operation.workspace,
                operation=operation,
                plant=plant,
                status=BulkPlantOperationResult.Status.SKIPPED,
                errors=row['conflicts'],
            )
            continue
        event = None
        location = None
        if request.action == BulkPlantOperation.Action.MOVE:
            location = move_specific_plant(plant, _move_data(request), user=user)
        elif request.action in ACTION_EVENTS:
            event = record_lifecycle_event(
                plant,
                user,
                OutcomeRequest(
                    ACTION_EVENTS[request.action],
                    occurred_at=request.occurred_at,
                    reason=request.reason,
                    reference=f'bulk-operation:{operation.pk}',
                ),
            )
        BulkPlantOperationResult.objects.create(
            workspace=operation.workspace,
            operation=operation,
            plant=plant,
            status=BulkPlantOperationResult.Status.APPLIED,
            lifecycle_event=event,
            location=location,
            nursery_observation=observation,
        )


def _post_repot_application(workspace, user, plants, request):
    """Post exact potting inputs and return their container observation facts."""
    from applications.services import create_application_draft, post_application  # pylint: disable=import-outside-toplevel

    draft = create_application_draft(
        workspace, user,
        _application_request(workspace, plants, request.action_payload['application']),
    )
    posted, _movements = post_application(draft, user)
    return {
        'container_item': request.action_payload['container_item'],
        'container_count': request.action_payload['container_count'],
        'input_application': posted,
    }


def _create_germinated_plant(operation, user, request, allocation, notes):
    """Create and audit one observed seedling at its source cell."""
    plant = SpecificPlant.objects.create(
        cell_planting=allocation,
        germinated=request.occurred_at,
        notes=notes,
    )
    location = SpecificPlantLocation.objects.create(
        specific_plant=plant,
        location_type=SpecificPlantLocation.SEED_TRAY_CELL,
        seed_tray_cell=allocation.cell,
        started=request.occurred_at,
    )
    event = record_germination_event(plant, user)
    BulkPlantOperationResult.objects.create(
        workspace=operation.workspace,
        operation=operation,
        plant=plant,
        status=BulkPlantOperationResult.Status.APPLIED,
        lifecycle_event=event,
        location=location,
    )


def _apply_germination(operation, user, request):
    """Create auditable plants per cell and reallocate each affected batch once."""
    quantities = {
        entry['cell_planting'].pk: entry['quantity']
        for entry in request.action_payload['germinations']
    }
    allocation_ids = list(quantities)
    allocations = list(
        SeedTrayCellPlanting.objects.select_for_update()
        .filter(pk__in=allocation_ids)
        .select_related('seed_tray_planting__batch', 'cell')
        .order_by('pk')
    )
    batches = {
        allocation.seed_tray_planting.batch_id: allocation.seed_tray_planting.batch
        for allocation in allocations
    }
    locked_batches = [
        lock_batch_with_plants(batches[batch_id]) for batch_id in sorted(batches)
    ]
    notes = request.action_payload.get('notes', '')
    for allocation in allocations:
        for _index in range(quantities[allocation.pk]):
            _create_germinated_plant(operation, user, request, allocation, notes)

    from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

    for batch in locked_batches:
        reallocate_batch(batch, user, 'bulk-germination')


@transaction.atomic
def _execute(workspace, user, request, digest):
    """Execute a new request atomically; callers handle idempotent races."""
    operation = _create_operation(workspace, user, request, digest)
    preview = preview_bulk_operation(workspace, request, lock=True)
    has_conflicts = preview['conflicts'] > 0
    if has_conflicts and request.atomicity == BulkPlantOperation.Atomicity.ALL_OR_NOTHING:
        raise BulkOperationConflict(preview)
    if preview['eligible'] == 0:
        raise BulkOperationConflict(preview)
    if request.action == BulkPlantOperation.Action.GERMINATE:
        _apply_germination(operation, user, request)
    else:
        _apply_plant_operation(operation, user, request, preview)
    return operation, False


def execute_bulk_operation(workspace, user, request):
    """Execute once, replaying an identical completed request after retries."""
    digest = request_digest(request)
    existing = (
        BulkPlantOperation.objects
        .filter(workspace=workspace, idempotency_key=request.idempotency_key)
        .first()
    )
    if existing is not None:
        if existing.request_digest != digest:
            raise ValidationError({'idempotency_key': 'That key was used for different work.'})
        return existing, True
    try:
        return _execute(workspace, user, request, digest)
    except IntegrityError as exc:
        existing = BulkPlantOperation.objects.get(
            workspace=workspace,
            idempotency_key=request.idempotency_key,
        )
        if existing.request_digest != digest:
            raise ValidationError({
                'idempotency_key': 'That key was used for different work.',
            }) from exc
        return existing, True


def concrete_request(**values):
    """Fill the one action time shared by every selected plant."""
    values['occurred_at'] = values.get('occurred_at') or timezone.now()
    values['plants'] = tuple(values.get('plants') or ())
    return BulkOperationRequest(**values)
