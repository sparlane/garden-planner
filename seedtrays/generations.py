"""Opening one fill of a seed tray, and the identity everything hangs off it.

A generation is the cultivation cycle a tray's cells are currently serving.
Opening one says the tray has been filled, and it is what a sowing joins and
what an application's cell target is attributed to. Only one is ever open per
tray, which is why nothing here has to ask the caller which fill they meant.

A generation migrated from records that predate the feature is flagged for
review, because those records genuinely cannot say whether the sowings grouped
under it were one fill. Reviewing it is an operator's statement, not an
inference, so it is recorded as its own fact.
"""

# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import SeedTrayGeneration, SeedTrayGenerationEvent


EventType = SeedTrayGenerationEvent.EventType


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def open_generation_for(tray):
    """Return the fill this tray is currently using, or None when it is empty."""
    return SeedTrayGeneration.objects.filter(
        tray=tray,
        status=SeedTrayGeneration.Status.OPEN,
    ).first()


def require_open_generation(tray, field='generation'):
    """Return this tray's open fill, refusing to guess when there is none."""
    generation = open_generation_for(tray)
    if generation is None:
        raise ValidationError({
            field: (
                f'Tray {tray.pk} has no open generation. Fill the tray before '
                'sowing into it or applying an input to its cells.'
            ),
        })
    return generation


def lock_generation(generation):
    """Reload one generation under a row lock, serialising its transitions."""
    return SeedTrayGeneration.objects.select_for_update().select_related(
        'tray',
        'workspace',
    ).get(pk=generation.pk)


def _record_event(generation, user, event_type, occurred_at, reason=''):
    """Append one immutable fact about this generation."""
    return SeedTrayGenerationEvent.objects.create(
        generation=generation,
        event_type=event_type,
        occurred_at=occurred_at,
        reason=reason,
        created_by=user if user is not None and user.is_authenticated else None,
    )


@transaction.atomic
def open_generation(tray, user, opened_at=None, notes=''):
    """Record that this tray has been filled and is ready to sow into."""
    existing = SeedTrayGeneration.objects.select_for_update().filter(
        tray=tray,
    ).order_by('-sequence')
    rows = list(existing)
    current = next(
        (row for row in rows if row.status == SeedTrayGeneration.Status.OPEN),
        None,
    )
    if current is not None:
        raise ValidationError({
            'tray': (
                f'Generation {current.code} is still open. Clean the tray before '
                'filling it again.'
            ),
        })
    sequence = (rows[0].sequence + 1) if rows else 1
    opened_at = opened_at or timezone.now()
    generation = SeedTrayGeneration(
        workspace=tray.workspace,
        tray=tray,
        code=f'TRAY-{tray.pk}-{sequence}',
        sequence=sequence,
        opened_at=opened_at,
        notes=notes,
        created_by=user if user is not None and user.is_authenticated else None,
    )
    generation.save()
    _record_event(generation, user, EventType.OPENED, opened_at, 'Tray filled.')
    return generation


@transaction.atomic
def review_generation(generation, user, reason):
    """Confirm a migrated fill really is one fill, unblocking its clean."""
    _require_reason(reason)
    generation = lock_generation(generation)
    if generation.review_state != SeedTrayGeneration.ReviewState.NEEDS_REVIEW:
        raise ValidationError({
            'review_state': 'This generation has already been reviewed.',
        })
    occurred_at = timezone.now()
    SeedTrayGeneration.objects.filter(pk=generation.pk).update(
        review_state=SeedTrayGeneration.ReviewState.NONE,
        updated=occurred_at,
    )
    _record_event(generation, user, EventType.REVIEWED, occurred_at, reason)
    generation.refresh_from_db()
    return generation
