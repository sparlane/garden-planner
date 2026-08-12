"""Transactional quarantine, treatment, and follow-up commands."""

# Health actions carry full reviewed audit inputs and coordinate several
# existing domain services.
# pylint: disable=too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-locals,too-many-branches

from uuid import uuid5

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from applications.models import InputApplication, InputApplicationTarget
from inventory.ledger import UnitMovementRequest, post_unit_movement
from inventory.models import StockMovement
from locations.models import Location
from plantings.cohorts import change_cohort
from plantings.lifecycle import (
    EventType,
    FINAL_STATES,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_lifecycle_event,
)
from plantings.models import (
    CohortOperation,
    PlantCohort,
    SpecificPlant,
    SpecificPlantLocation,
)

from .availability import case_is_active
from .models import (
    HealthFollowUp,
    HealthObservation,
    HealthTreatment,
    QuarantineAction,
    QuarantineActionResult,
    QuarantineCase,
    QuarantineMember,
)


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _require_reason(reason):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def _validate_destination(workspace, destination, action):
    if destination is None:
        return
    if destination.workspace_id != workspace.pk or not destination.active:
        raise ValidationError({'destination': 'Choose an active location in this workspace.'})
    quarantine = destination.location_type == Location.LocationType.QUARANTINE
    if action == QuarantineAction.Action.QUARANTINE and not quarantine:
        raise ValidationError({'destination': 'Quarantined stock must move to a quarantine location.'})
    if action == QuarantineAction.Action.RELEASE and quarantine:
        raise ValidationError({'destination': 'Released stock must leave quarantine.'})


def _member_rows(case, lock=False):
    plants = SpecificPlant.objects.filter(
        quarantine_memberships__case=case,
    ).order_by('pk')
    cohorts = PlantCohort.objects.filter(
        quarantine_memberships__case=case,
    ).order_by('pk')
    if lock:
        plants = plants.select_for_update()
        cohorts = cohorts.select_for_update()
    return list(plants), list(cohorts)


def _validate_members(case, plants, cohorts, quarantine=False):
    """Require live plants and unchanged whole-cohort membership."""
    if quarantine:
        finished = [
            plant.pk for plant in plants
            if plant_lifecycle_summary(plant).state in FINAL_STATES
        ]
        if finished:
            raise ValidationError({'plants': f'Finished plants cannot be quarantined: {finished}.'})
    recorded = {
        row.cohort_id: row.quantity
        for row in case.members.filter(cohort__isnull=False)
    }
    changed = [
        cohort.pk for cohort in cohorts if cohort.quantity != recorded[cohort.pk]
    ]
    if changed:
        raise ValidationError({
            'cohorts': (
                f'Cohort quantities changed after review: {changed}. '
                'Split or inspect them again.'
            ),
        })


def _tray_groups(plants):
    placements = list(
        SpecificPlantLocation.objects.filter(
            specific_plant__in=plants, ended__isnull=True,
        ).select_related('seed_tray_cell__tray__inventory_unit')
    )
    by_plant = {row.specific_plant_id: row for row in placements}
    trays = {}
    for plant in plants:
        placement = by_plant.get(plant.pk)
        if placement and placement.seed_tray_cell_id:
            tray = placement.seed_tray_cell.tray
            trays.setdefault(tray.pk, tray)
    return by_plant, trays


def _move_members(workspace, user, action, plants, cohorts, destination, reason):
    """Move direct plants, whole cohorts, and fully selected tray carriers."""
    from plantings.rest import move_specific_plant  # pylint: disable=import-outside-toplevel

    by_plant, trays = _tray_groups(plants)
    selected_ids = {plant.pk for plant in plants}
    tray_movements = {}
    for tray_id, tray in trays.items():
        riding = set(SpecificPlantLocation.objects.filter(
            seed_tray_cell__tray_id=tray_id, ended__isnull=True,
        ).values_list('specific_plant_id', flat=True))
        if not riding.issubset(selected_ids):
            raise ValidationError({
                'destination': f'Tray {tray_id} also carries unreviewed plants.',
            })
        tray_movements[tray_id] = post_unit_movement(
            workspace, user,
            UnitMovementRequest(
                unit=tray.inventory_unit,
                movement_type=StockMovement.MovementType.TRANSFER,
                destination=destination,
                occurred_at=action.occurred_at,
                reason=reason,
                reference=f'quarantine-action:{action.pk}',
            ),
        )
    for plant in plants:
        placement = by_plant.get(plant.pk)
        movement = None
        location = None
        if placement and placement.seed_tray_cell_id:
            movement = tray_movements[placement.seed_tray_cell.tray_id]
        else:
            location = move_specific_plant(plant, {
                'location_type': SpecificPlantLocation.LOCATION,
                'location': destination,
                'started': action.occurred_at,
                'notes': reason,
            }, user=user)
        QuarantineActionResult.objects.create(
            action=action, plant=plant,
            plant_location=location, stock_movement=movement,
        )
    for cohort in cohorts:
        cohort, operation = change_cohort(
            workspace, user, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.MOVE,
            idempotency_key=uuid5(action.idempotency_key, f'cohort:{cohort.pk}:move'),
            occurred_at=action.occurred_at,
            reason=reason,
            location=destination,
            allow_quarantined=True,
        )
        QuarantineActionResult.objects.create(
            action=action, cohort=cohort, cohort_operation=operation,
        )


def _existing_action(workspace, key, expected_action, case=None):
    existing = QuarantineAction.objects.filter(
        workspace=workspace, idempotency_key=key,
    ).first()
    wrong_case = case is not None and existing and existing.case_id != case.pk
    if existing and (existing.action != expected_action or wrong_case):
        raise ValidationError({'idempotency_key': 'That key was used for different work.'})
    return existing


@transaction.atomic
def quarantine_observation(
        workspace, user, observation, *, idempotency_key, reason,
        occurred_at=None, destination=None,
):
    """Open one case over an observation's exact reviewed stock."""
    _require_reason(reason)
    _validate_destination(workspace, destination, QuarantineAction.Action.QUARANTINE)
    existing = _existing_action(
        workspace, idempotency_key, QuarantineAction.Action.QUARANTINE,
    )
    if existing:
        if existing.case.observation_id != observation.pk:
            raise ValidationError({'idempotency_key': 'That key was used for different work.'})
        return existing.case, existing
    observation = HealthObservation.objects.select_for_update(of=('self',)).filter(
        workspace=workspace, pk=observation.pk, correction__isnull=True,
    ).first()
    if observation is None:
        raise ValidationError({'observation': 'Choose an effective observation in this workspace.'})
    affected = list(observation.affected_stock.all())
    if not affected:
        raise ValidationError({'observation': 'There is no affected stock to quarantine.'})
    case = QuarantineCase.objects.create(
        workspace=workspace, observation=observation,
        reason=reason, created_by=_actor(user),
    )
    QuarantineMember.objects.bulk_create([
        QuarantineMember(
            case=case, plant_id=row.plant_id,
            cohort_id=row.cohort_id, quantity=row.quantity,
        )
        for row in affected
    ])
    plants, cohorts = _member_rows(case, lock=True)
    _validate_members(case, plants, cohorts, quarantine=True)
    action = QuarantineAction.objects.create(
        workspace=workspace, case=case, idempotency_key=idempotency_key,
        action=QuarantineAction.Action.QUARANTINE,
        occurred_at=occurred_at or timezone.now(), reason=reason,
        destination=destination, created_by=_actor(user),
    )
    if destination:
        _move_members(workspace, user, action, plants, cohorts, destination, reason)
    return case, action


def _cull_members(workspace, user, action, plants, cohorts):
    for plant in plants:
        event = record_lifecycle_event(
            plant, user,
            OutcomeRequest(
                EventType.CULLED, occurred_at=action.occurred_at,
                reason=action.reason, reference=f'quarantine-action:{action.pk}',
            ),
        )
        QuarantineActionResult.objects.create(
            action=action, plant=plant, lifecycle_event=event,
        )
    for cohort in cohorts:
        cohort, operation = change_cohort(
            workspace, user, cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            idempotency_key=uuid5(action.idempotency_key, f'cohort:{cohort.pk}:cull'),
            occurred_at=action.occurred_at, reason=action.reason,
            quantity=cohort.quantity,
            allow_quarantined=True,
        )
        QuarantineActionResult.objects.create(
            action=action, cohort=cohort, cohort_operation=operation,
        )


def _escalate_case(workspace, user, action, plants, cohorts):
    """Create an immediate high-priority task linked to the reviewed stock."""
    from work.models import WorkTaskType  # pylint: disable=import-outside-toplevel
    from work.services import create_manual_task  # pylint: disable=import-outside-toplevel

    targets = [
        (action.case.observation, f'Health observation {action.case.observation_id}', '/health')
    ]
    targets.extend(
        (plant, f'Plant {plant.pk}', f'/plantings/plants/{plant.pk}')
        for plant in plants
    )
    targets.extend(
        (cohort, f'Cohort {cohort.pk}', f'/plantings/cohorts/{cohort.pk}')
        for cohort in cohorts
    )
    create_manual_task(workspace, user, {
        'task_type': WorkTaskType.HEALTH_INSPECTION,
        'title': f'Escalated health issue #{action.case.observation_id}',
        'notes': action.reason,
        'priority': 100,
        'due_start': action.occurred_at,
        'due_end': action.occurred_at,
        'source_snapshot': {
            'quarantine_case': action.case_id,
            'quarantine_action': action.pk,
        },
    }, targets=targets)


@transaction.atomic
def act_on_quarantine(
        workspace, user, case, *, action_name, idempotency_key, reason,
        occurred_at=None, destination=None,
):
    """Release, escalate, or cull one independently locked active case."""
    if action_name not in {
            QuarantineAction.Action.RELEASE,
            QuarantineAction.Action.ESCALATE,
            QuarantineAction.Action.CULL,
    }:
        raise ValidationError({'action': 'Select release, escalate, or cull.'})
    _require_reason(reason)
    _validate_destination(workspace, destination, action_name)
    existing = _existing_action(workspace, idempotency_key, action_name, case)
    if existing:
        return existing
    case = QuarantineCase.objects.select_for_update().filter(
        workspace=workspace, pk=case.pk,
    ).first()
    if case is None:
        raise ValidationError({'case': 'The quarantine case does not belong to this workspace.'})
    if not case_is_active(case):
        raise ValidationError({'case': 'This quarantine case is already closed.'})
    plants, cohorts = _member_rows(case, lock=True)
    _validate_members(case, plants, cohorts)
    action = QuarantineAction.objects.create(
        workspace=workspace, case=case, idempotency_key=idempotency_key,
        action=action_name, occurred_at=occurred_at or timezone.now(),
        reason=reason, destination=destination, created_by=_actor(user),
    )
    if destination:
        _move_members(workspace, user, action, plants, cohorts, destination, reason)
    if action_name == QuarantineAction.Action.CULL:
        _cull_members(workspace, user, action, plants, cohorts)
    elif action_name == QuarantineAction.Action.ESCALATE:
        _escalate_case(workspace, user, action, plants, cohorts)
    return action


@transaction.atomic
def link_treatment(
        workspace, user, observation, application, *, follow_up_due_at=None, notes='',
):
    """Link one posted concrete-target application exactly once."""
    observation = HealthObservation.objects.select_for_update().get(
        workspace=workspace, pk=observation.pk,
    )
    application = InputApplication.objects.select_for_update().filter(
        workspace=workspace, pk=application.pk,
    ).first()
    if application is None or application.status != InputApplication.Status.POSTED:
        raise ValidationError({'application': 'Choose a posted application in this workspace.'})
    if hasattr(application, 'health_treatment'):
        raise ValidationError({'application': 'This application is already linked as a treatment.'})
    allowed = set(observation.affected_stock.filter(
        plant__isnull=False,
    ).values_list('plant_id', flat=True))
    allowed.update(
        ('cohort', cohort_id)
        for cohort_id in observation.affected_stock.filter(
            cohort__isnull=False,
        ).values_list('cohort_id', flat=True)
    )
    targets = InputApplicationTarget.objects.filter(
        line__application=application,
    ).distinct()
    if not targets.exists():
        raise ValidationError({'application': 'The application has no concrete treatment targets.'})
    for target in targets:
        identity = (
            target.specific_plant_id
            if target.target_type == InputApplicationTarget.TargetType.SPECIFIC_PLANT
            else ('cohort', target.plant_cohort_id)
            if target.target_type == InputApplicationTarget.TargetType.PLANT_COHORT
            else None
        )
        if identity is None or identity not in allowed:
            raise ValidationError({
                'application': 'Every treatment target must be in the reviewed affected set.',
            })
    return HealthTreatment.objects.create(
        workspace=workspace, observation=observation, application=application,
        follow_up_due_at=follow_up_due_at, notes=notes, created_by=_actor(user),
    )


@transaction.atomic
def record_follow_up(
        workspace, user, observation, *, treatment=None, corrects=None, **values,
):
    """Append one effective follow-up or a reasoned replacement."""
    observation = HealthObservation.objects.select_for_update().get(
        workspace=workspace, pk=observation.pk,
    )
    if treatment is not None:
        treatment = HealthTreatment.objects.select_for_update().filter(
            workspace=workspace, pk=treatment.pk, observation=observation,
        ).first()
        if treatment is None:
            raise ValidationError({'treatment': 'The treatment belongs to another observation.'})
    if corrects is not None:
        corrects = HealthFollowUp.objects.select_for_update().filter(
            workspace=workspace, pk=corrects.pk, observation=observation,
        ).first()
        if corrects is None or hasattr(corrects, 'correction'):
            raise ValidationError({'corrects': 'Choose one uncorrected follow-up.'})
        treatment = corrects.treatment
    else:
        effective = HealthFollowUp.objects.filter(
            observation=observation, treatment=treatment, correction__isnull=True,
        )
        if effective.exists():
            raise ValidationError({'follow_up': 'This follow-up has already been recorded.'})
    return HealthFollowUp.objects.create(
        workspace=workspace, observation=observation, treatment=treatment,
        corrects=corrects, created_by=_actor(user), **values,
    )
