"""Transactional commands for acknowledging and acting on nursery work."""

# Audit helpers carry actor, reason, change, and idempotency context together;
# the action dispatcher deliberately keeps each supported transition visible.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-statements

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import WorkTask, WorkTaskHistory, WorkTaskLink
from .projections import next_recurrence, projected_tasks, target_identity


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _history(task, action, user, reason='', changes=None, idempotency_key=''):
    if idempotency_key:
        existing = task.history.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    return WorkTaskHistory.objects.create(
        task=task, action=action, actor=_actor(user), reason=reason,
        changes=changes or {}, idempotency_key=idempotency_key,
    ), True


def _link(task, role, target, label, url='', snapshot=None):
    content_type, object_id = target_identity(target)
    return WorkTaskLink.objects.create(
        task=task, role=role, content_type=content_type, object_id=object_id,
        label=label, url=url, snapshot=snapshot or {},
    )


@transaction.atomic
def create_manual_task(workspace, user, values, targets=()):
    """Create one manual task and its initial immutable history."""
    task = WorkTask.objects.create(
        workspace=workspace,
        key=f'manual:{uuid4()}',
        origin=WorkTask.Origin.MANUAL,
        created_by=_actor(user),
        **values,
    )
    for target, label, url in targets:
        _link(task, WorkTaskLink.Role.TARGET, target, label, url)
    _history(task, 'created', user)
    return task


@transaction.atomic
def acknowledge_projection(workspace, user, key):
    """Snapshot the current form of one generated occurrence exactly once."""
    existing = WorkTask.objects.select_for_update().filter(workspace=workspace, key=key).first()
    if existing:
        return existing
    projection = next((row for row in projected_tasks(workspace) if row.key == key), None)
    if projection is None:
        raise ValidationError({'key': 'This generated task changed or is no longer due.'})
    try:
        task = WorkTask.objects.create(
            workspace=workspace, key=projection.key,
            origin=WorkTask.Origin.GENERATED, rule=projection.rule,
            task_type=projection.task_type, title=projection.title,
            priority=projection.priority, due_start=projection.due_start,
            due_end=projection.due_end, assignee=projection.assignee,
            source_snapshot=projection.source_snapshot, created_by=_actor(user),
        )
    except IntegrityError:
        return WorkTask.objects.select_for_update().get(workspace=workspace, key=key)
    for target in projection.targets:
        _link(task, WorkTaskLink.Role.TARGET, target.target, target.label, target.url)
    _history(task, 'acknowledged', user)
    return task


def _result_target(workspace, values):
    try:
        content_type = ContentType.objects.get(
            app_label=values['app_label'], model=values['model'],
        )
        target = content_type.get_object_for_this_type(pk=values['object_id'])
    except (ContentType.DoesNotExist, ObjectDoesNotExist, KeyError, ValueError) as exc:
        raise ValidationError({'results': 'A linked result does not exist.'}) from exc
    target_workspace_id = getattr(target, 'workspace_id', None)
    if target_workspace_id != workspace.pk:
        raise ValidationError({'results': 'A linked result belongs to another workspace.'})
    return target


def _next_manual_occurrence(task, user):
    recurrence = task.recurrence
    if not recurrence:
        return None
    next_start = next_recurrence(
        task.workspace, task.due_start, recurrence['frequency'],
        recurrence.get('interval', 1), recurrence.get('weekdays', ()),
    )
    duration = task.due_end - task.due_start
    key = f'{task.key}:next:{next_start.isoformat()}'
    following, created = WorkTask.objects.get_or_create(
        workspace=task.workspace, key=key,
        defaults={
            'origin': WorkTask.Origin.MANUAL,
            'task_type': task.task_type,
            'title': task.title,
            'notes': task.notes,
            'priority': task.priority,
            'due_start': next_start,
            'due_end': next_start + duration,
            'assignee': task.assignee,
            'recurrence': recurrence,
            'parent': task,
            'created_by': _actor(user),
        },
    )
    if created:
        for link in task.links.filter(role=WorkTaskLink.Role.TARGET):
            WorkTaskLink.objects.create(
                task=following, role=link.role, content_type=link.content_type,
                object_id=link.object_id, label=link.label, url=link.url,
                snapshot=link.snapshot,
            )
        _history(following, 'created', user, changes={'recurs_from': task.pk})
    return following


@transaction.atomic
def act_on_task(workspace, user, task_id, action, values):  # pylint: disable=too-many-branches
    """Apply one explicit idempotent state transition under a row lock."""
    task = WorkTask.objects.select_for_update().filter(workspace=workspace, pk=task_id).first()
    if task is None:
        raise ValidationError({'task': 'The task does not belong to this workspace.'})
    idempotency_key = values.get('idempotency_key', '')
    if idempotency_key and task.history.filter(idempotency_key=idempotency_key).exists():
        return task
    now = timezone.now()
    changes = {}
    reason = values.get('reason', '').strip()
    if action in {'complete', 'skip'} and task.status in {WorkTask.Status.COMPLETED, WorkTask.Status.SKIPPED}:
        raise ValidationError({'status': 'This task is already closed.'})
    if action == 'assign':
        assignee_id = values.get('assignee')
        assignee = None
        if assignee_id is not None:
            assignee = get_user_model().objects.filter(pk=assignee_id, is_active=True).first()
            if assignee is None:
                raise ValidationError({'assignee': 'Choose an active user.'})
        task.assignee = assignee
        changes['assignee'] = assignee.pk if assignee else None
    elif action == 'claim':
        task.assignee = user
        changes['assignee'] = user.pk
    elif action == 'snooze':
        until = values.get('until')
        if until is None or until <= now:
            raise ValidationError({'until': 'Snooze until a future time.'})
        task.status = WorkTask.Status.SNOOZED
        task.snoozed_until = until
        changes['until'] = until.isoformat()
    elif action == 'complete':
        task.status = WorkTask.Status.COMPLETED
        task.completed_at = now
        task.snoozed_until = None
        for result in values.get('results', ()):
            target = _result_target(workspace, result)
            _link(
                task, WorkTaskLink.Role.RESULT, target,
                result.get('label') or str(target), result.get('url', ''),
            )
    elif action == 'skip':
        if not reason:
            raise ValidationError({'reason': 'Explain why this task was skipped.'})
        task.status = WorkTask.Status.SKIPPED
        task.skipped_at = now
        task.snoozed_until = None
    elif action == 'reopen':
        if task.status not in {WorkTask.Status.COMPLETED, WorkTask.Status.SKIPPED}:
            raise ValidationError({'status': 'Only completed or skipped work can be reopened.'})
        if not reason:
            raise ValidationError({'reason': 'Explain why this task was reopened.'})
        task.status = WorkTask.Status.OPEN
        task.completed_at = None
        task.skipped_at = None
    else:
        raise ValidationError({'action': 'Choose a supported task action.'})
    task.save()
    _history(task, action, user, reason, changes, idempotency_key)
    if action in {'complete', 'skip'}:
        _next_manual_occurrence(task, user)
    return task
