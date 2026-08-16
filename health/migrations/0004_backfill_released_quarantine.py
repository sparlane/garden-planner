"""Give already-released quarantine returns the fact that released them.

Releasing a case used to close it without recording anything against the plants
it held, so every plant a customer returned into quarantine and an operator then
released stayed in the `quarantined` lifecycle state: unsellable, and counted as
live unresolved stock forever. The operator did decide to release it, and the
`release` action says when and why, so the missing fact is derivable rather than
a judgement call. Backfill it, and refuse to deploy over any plant whose history
cannot be repaired truthfully.
"""

from django.db import migrations


#: The facts lifecycle state is replayed from, frozen at this migration.
STATE_EVENT_TYPES = (
    'germinated',
    'ready',
    'retained',
    'donated',
    'failed',
    'lost',
    'culled',
    'harvest_finished',
    'sold',
    'returned_available',
    'returned_quarantined',
    'returned_discarded',
    'released_available',
)

RETURNED_QUARANTINED = 'returned_quarantined'
RELEASED_AVAILABLE = 'released_available'
RELEASE = 'release'


def describe_rows(row_ids, count):
    """Return a bounded description of rows that failed the audit."""
    shown = sorted(row_ids)[:20]
    suffix = '' if count <= len(shown) else f' (first 20 of {count})'
    return f'{shown}{suffix}'


def _stranded_returns(event_model):
    """Return the quarantined return that still decides each stuck plant.

    A plant is stuck when the last of its surviving state-changing facts is a
    quarantined return, which is the same replay `plantings.lifecycle` performs
    over the same events.
    """
    plant_ids = set(
        event_model.objects
        .filter(event_type=RETURNED_QUARANTINED, reversal__isnull=True)
        .values_list('plant_id', flat=True)
    )
    if not plant_ids:
        return {}
    latest = {}
    for event in event_model.objects.filter(
            plant_id__in=plant_ids,
            event_type__in=STATE_EVENT_TYPES,
            reversal__isnull=True,
    ).order_by('occurred_at', 'pk'):
        latest[event.plant_id] = event
    return {
        plant_id: event
        for plant_id, event in latest.items()
        if event.event_type == RETURNED_QUARANTINED
    }


def _closing_releases(member_model, action_model, returns):
    """Return the release action that closed each stuck plant's case.

    A plant whose case is still open is absent: nothing was lost there, and the
    ordinary health workflow now resolves it.
    """
    cases_by_plant = {}
    for plant_id, case_id in member_model.objects.filter(
            plant_id__in=list(returns),
    ).values_list('plant_id', 'case_id'):
        cases_by_plant.setdefault(plant_id, []).append(case_id)
    every_case = {case_id for cases in cases_by_plant.values() for case_id in cases}
    releases = {}
    for action in action_model.objects.filter(
            case_id__in=every_case, action=RELEASE,
    ).order_by('occurred_at', 'pk'):
        releases.setdefault(action.case_id, []).append(action)
    closing = {}
    for plant_id, returned in returns.items():
        candidates = [
            action
            for case_id in cases_by_plant.get(plant_id, [])
            for action in releases.get(case_id, [])
            if action.occurred_at >= returned.occurred_at
        ]
        if candidates:
            closing[plant_id] = max(
                candidates, key=lambda action: (action.occurred_at, action.pk),
            )
    return closing


def _unrepairable(event_model, closing):
    """Return the plants whose release cannot be appended in order.

    The backfilled fact happens when the operator released the case, so a plant
    carrying anything later than that has a history no honest append can join.
    """
    latest_events = {}
    for event in event_model.objects.filter(
            plant_id__in=list(closing),
    ).order_by('occurred_at', 'pk'):
        latest_events[event.plant_id] = event
    return {
        plant_id
        for plant_id, action in closing.items()
        if latest_events[plant_id].occurred_at > action.occurred_at
    }


def backfill_released_quarantine(apps, _schema_editor):
    """Record the release that closed each stranded plant's quarantine case."""
    event_model = apps.get_model('plantings', 'PlantLifecycleEvent')
    member_model = apps.get_model('health', 'QuarantineMember')
    action_model = apps.get_model('health', 'QuarantineAction')
    result_model = apps.get_model('health', 'QuarantineActionResult')

    returns = _stranded_returns(event_model)
    if not returns:
        return
    closing = _closing_releases(member_model, action_model, returns)
    blocked = _unrepairable(event_model, closing)
    if blocked:
        raise RuntimeError(
            'Quarantine release backfill failed. These plants were released '
            'from quarantine but carry later facts, so the release cannot be '
            'appended in the order it happened. Correct or re-record their '
            'histories before retrying the migration: '
            f'stranded SpecificPlant IDs: {describe_rows(blocked, len(blocked))}'
        )
    for plant_id, action in closing.items():
        returned = returns[plant_id]
        event = event_model.objects.create(
            workspace_id=returned.workspace_id,
            plant_id=plant_id,
            batch_id=returned.batch_id,
            event_type=RELEASED_AVAILABLE,
            occurred_at=action.occurred_at,
            reason=action.reason,
            reference=f'quarantine-action:{action.pk}',
            created_by_id=action.created_by_id,
        )
        result_model.objects.create(
            action=action, plant_id=plant_id, lifecycle_event=event,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('health', '0003_healthtreatment_healthfollowup_quarantineaction_and_more'),
        ('plantings', '0038_released_available_event'),
    ]

    operations = [
        migrations.RunPython(
            backfill_released_quarantine,
            migrations.RunPython.noop,
        ),
    ]
