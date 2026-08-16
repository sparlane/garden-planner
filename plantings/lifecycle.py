"""Append-only lifecycle history and derived state for individual plants.

Current state is replayed from `PlantLifecycleEvent` rows every time it is
asked for, so the APIs and the batch reports share one derivation and no stored
status can drift away from the recorded facts.
"""

# pylint: disable=duplicate-code

from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, F, OuterRef, Subquery, Value, When
from django.utils import timezone

from .models import PlantLifecycleEvent, SpecificPlant, SpecificPlantLocation


EventType = PlantLifecycleEvent.EventType


class LifecycleState(models.TextChoices):
    """The derived condition of one individual plant.

    These values deliberately echo the outcome event that produces them; the
    two vocabularies are never mixed inside one lookup.
    """

    GROWING = 'growing', 'Growing'
    AVAILABLE = 'available', 'Available'
    RETAINED = 'retained', 'Retained'
    DONATED = 'donated', 'Donated'
    FAILED = 'failed', 'Failed'
    LOST = 'lost', 'Lost'
    CULLED = 'culled', 'Culled'
    HARVESTED = 'harvested', 'Harvested'
    SOLD = 'sold', 'Sold'
    QUARANTINED = 'quarantined', 'Returned quarantined'
    DISCARDED = 'discarded', 'Returned discarded'


#: The state each fact leaves behind. `transplanted` and `corrected` are absent
#: because they record something that happened without changing the condition.
STATE_AFTER = {
    EventType.GERMINATED: LifecycleState.GROWING,
    EventType.READY: LifecycleState.AVAILABLE,
    EventType.RETAINED: LifecycleState.RETAINED,
    EventType.DONATED: LifecycleState.DONATED,
    EventType.FAILED: LifecycleState.FAILED,
    EventType.LOST: LifecycleState.LOST,
    EventType.CULLED: LifecycleState.CULLED,
    EventType.HARVEST_FINISHED: LifecycleState.HARVESTED,
    EventType.SOLD: LifecycleState.SOLD,
    EventType.RETURNED_AVAILABLE: LifecycleState.AVAILABLE,
    EventType.RETURNED_QUARANTINED: LifecycleState.QUARANTINED,
    EventType.RETURNED_DISCARDED: LifecycleState.DISCARDED,
    EventType.RELEASED_AVAILABLE: LifecycleState.AVAILABLE,
    EventType.HELD_BACK: LifecycleState.GROWING,
    EventType.RETENTION_ENDED: LifecycleState.GROWING,
}

#: The states each fact may be recorded from. `germinated` is absent because it
#: is only valid for a plant with no history at all.
#:
#: A quarantined plant is one a customer returned as diseased or damaged, so it
#: accepts the facts an assessment can honestly reach: release when it recovers,
#: retention when it needs growing on instead, and the three ways of losing it.
#: Donation, harvest, planting out and sale stay closed, because handing a plant
#: the nursery has not cleared to somebody else is the thing quarantine exists to
#: prevent — release it first and the ordinary transitions apply.
#:
#: `held_back` and `retention_ended` are the backward facts: taking stock off
#: offer, and returning a plant kept for the operation's own use to production.
#: Both land in `growing` rather than restoring whatever the plant was before,
#: because deciding a plant is offerable is a judgement somebody makes, and the
#: `ready` that follows records it. That also keeps `ready` permitted only from
#: `growing`, so a repeated cycle reads as separate offers rather than as one
#: fact recorded twice.
ALLOWED_FROM = {
    EventType.READY: {LifecycleState.GROWING},
    EventType.TRANSPLANTED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
    },
    EventType.RETAINED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.QUARANTINED,
    },
    EventType.FAILED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
        LifecycleState.QUARANTINED,
    },
    EventType.LOST: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
        LifecycleState.QUARANTINED,
    },
    EventType.CULLED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
        LifecycleState.QUARANTINED,
    },
    EventType.DONATED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
    },
    EventType.HARVEST_FINISHED: {
        LifecycleState.GROWING,
        LifecycleState.AVAILABLE,
        LifecycleState.RETAINED,
    },
    EventType.SOLD: {LifecycleState.AVAILABLE},
    EventType.RETURNED_AVAILABLE: {LifecycleState.SOLD},
    EventType.RETURNED_QUARANTINED: {LifecycleState.SOLD},
    EventType.RETURNED_DISCARDED: {LifecycleState.SOLD},
    EventType.RELEASED_AVAILABLE: {LifecycleState.QUARANTINED},
    EventType.HELD_BACK: {LifecycleState.AVAILABLE},
    EventType.RETENTION_ENDED: {LifecycleState.RETAINED},
}

#: States that resolve a plant. Retained is final for availability without
#: ending biological growth, so failure or harvest may still follow it.
FINAL_STATES = {
    LifecycleState.RETAINED,
    LifecycleState.DONATED,
    LifecycleState.FAILED,
    LifecycleState.LOST,
    LifecycleState.CULLED,
    LifecycleState.HARVESTED,
    LifecycleState.SOLD,
    LifecycleState.DISCARDED,
}

#: States in which a plant is offerable to somebody else.
SELLABLE_STATES = {LifecycleState.AVAILABLE}

#: Facts that end a plant's presence in a physical location. A retained plant
#: keeps growing where it is, so it is not listed. Of the three return outcomes
#: only `returned_discarded` is, because it is the only one that destroys the
#: plant: an available or quarantined return is physically back on a bench, and
#: `post_return` opens the location that says which one. `released_available`
#: leaves the plant exactly where the quarantine put it until somebody moves it,
#: and neither backward fact is listed either: holding stock back or ending a
#: retention changes what the nursery will do with a plant, not where it stands.
CLOSES_LOCATION = {
    EventType.DONATED,
    EventType.FAILED,
    EventType.LOST,
    EventType.CULLED,
    EventType.HARVEST_FINISHED,
    EventType.SOLD,
    EventType.RETURNED_DISCARDED,
}

#: Facts an operator may record directly against a plant. `released_available`
#: is absent on purpose: releasing is the health workflow's decision, and
#: `act_on_quarantine` closes the case in the same transaction that records it.
#: Recorded directly it would leave a plant available while its case stayed open
#: and the quarantine overlay kept refusing the sale.
OUTCOME_EVENTS = (
    EventType.READY,
    EventType.RETAINED,
    EventType.FAILED,
    EventType.LOST,
    EventType.CULLED,
    EventType.DONATED,
    EventType.HARVEST_FINISHED,
    EventType.HELD_BACK,
    EventType.RETENTION_ENDED,
)

#: Facts recording that a plant's condition changed backwards. They are not
#: corrections: a correction says a fact was never true, while these say it was
#: true and then the situation changed, so both intervals stay in the history.
#: Each requires a stated reason, because a plant leaving offer without one is
#: indistinguishable from a mis-click by the time anybody reads the trail.
BACKWARD_EVENTS = (
    EventType.HELD_BACK,
    EventType.RETENTION_ENDED,
)


class LifecycleSummary(NamedTuple):
    """One plant's derived condition and final-outcome metadata."""

    state: str
    sellable: bool
    final_outcome: object = None
    final_outcome_at: object = None


class OutcomeRequest(NamedTuple):
    """Caller intent for one recorded lifecycle fact."""

    event_type: str
    occurred_at: object = None
    reason: str = ''
    reference: str = ''

    def at(self, default):
        """Return this request with a concrete time for the whole action."""
        if self.occurred_at is not None:
            return self
        return OutcomeRequest(
            event_type=self.event_type,
            occurred_at=default,
            reason=self.reason,
            reference=self.reference,
        )


def is_final(state):
    """Return whether a derived state resolves the plant."""
    return state in FINAL_STATES


def states_without_exits(state_after=None, allowed_from=None, final_states=None):
    """Return the reachable non-final states that no fact may be recorded from.

    A state that neither resolves a plant nor admits any next fact is a dead
    end: the plant is stuck there, counted as live unresolved stock forever, and
    the only way out is a correction claiming it was never there at all. That is
    what `quarantined` was until releasing became a fact of its own.

    The three vocabularies default to this module's, and are arguments so a
    proposed one can be checked before it ships.
    """
    state_after = STATE_AFTER if state_after is None else state_after
    allowed_from = ALLOWED_FROM if allowed_from is None else allowed_from
    final_states = FINAL_STATES if final_states is None else final_states
    # A plant with no history at all is growing, so growing is always reachable.
    reachable = {LifecycleState.GROWING, *state_after.values()}
    with_exits = {state for sources in allowed_from.values() for state in sources}
    return {
        state for state in reachable
        if state not in final_states and state not in with_exits
    }


def derive_state(events):
    """Replay one plant's facts into its current summary.

    Reversed events and the corrections that reverse them are both skipped, so
    a correction restores whatever the surviving facts imply.
    """
    corrected_ids = {
        event.reversal_of_id
        for event in events
        if event.reversal_of_id is not None
    }
    state = LifecycleState.GROWING
    outcome = None
    outcome_at = None
    for event in sorted(events, key=lambda event: (event.occurred_at, event.pk)):
        if event.pk in corrected_ids:
            continue
        next_state = STATE_AFTER.get(event.event_type)
        if next_state is None:
            continue
        state = next_state
        if is_final(state):
            outcome, outcome_at = event.event_type, event.occurred_at
        else:
            outcome, outcome_at = None, None
    return LifecycleSummary(
        state=state,
        sellable=state in SELLABLE_STATES,
        final_outcome=outcome,
        final_outcome_at=outcome_at,
    )


#: The facts `derive_state` reads. Every other event type records something that
#: happened without changing the condition it leaves behind.
STATE_EVENT_TYPES = tuple(STATE_AFTER)


def effective_state_events(plant_ref):
    """Return one plant's surviving state-changing facts, latest first.

    This is the database's view of the same events `derive_state` replays: a
    correction reverses its target, and `reversal__isnull` reads that reverse
    relation, so a reversed fact and the correction itself are both excluded.
    """
    return (
        PlantLifecycleEvent.objects
        .filter(
            plant=plant_ref,
            event_type__in=STATE_EVENT_TYPES,
            reversal__isnull=True,
        )
        .order_by('-occurred_at', '-pk')
    )


def with_lifecycle_state(queryset):
    """Annotate derived lifecycle state onto a `SpecificPlant` queryset.

    The replay in `derive_state` keeps the last surviving state-changing fact,
    so taking the newest one here reaches the same answer while leaving the
    result filterable, sortable, countable, and pageable in the database. The
    two derivations share `STATE_AFTER`, `FINAL_STATES`, and `SELLABLE_STATES`
    so neither can quietly describe a different vocabulary from the other.
    """
    events = effective_state_events(OuterRef('pk'))
    return (
        queryset
        .annotate(
            last_state_event=Subquery(events.values('event_type')[:1]),
            last_state_at=Subquery(events.values('occurred_at')[:1]),
        )
        .annotate(
            lifecycle_state=Case(
                *[
                    When(last_state_event=event_type, then=Value(state))
                    for event_type, state in STATE_AFTER.items()
                ],
                default=Value(LifecycleState.GROWING),
                output_field=models.CharField(),
            ),
        )
        .annotate(
            sellable=Case(
                When(
                    lifecycle_state__in=sorted(SELLABLE_STATES),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=models.BooleanField(),
            ),
            final_outcome=Case(
                When(lifecycle_state__in=sorted(FINAL_STATES), then=F('last_state_event')),
                default=Value(None),
                output_field=models.CharField(null=True),
            ),
            final_outcome_at=Case(
                When(lifecycle_state__in=sorted(FINAL_STATES), then=F('last_state_at')),
                default=Value(None),
                output_field=models.DateTimeField(null=True),
            ),
        )
    )


def plant_lifecycle_summary(plant):
    """Return one plant's summary, reusing prefetched events when present."""
    return derive_state(list(plant.lifecycle_events.all()))


def lifecycle_summaries(plant_ids):
    """Return a summary per plant id, reading every event in one query."""
    grouped = {plant_id: [] for plant_id in plant_ids}
    events = PlantLifecycleEvent.objects.filter(plant_id__in=list(grouped))
    for event in events:
        grouped[event.plant_id].append(event)
    return {
        plant_id: derive_state(plant_events)
        for plant_id, plant_events in grouped.items()
    }


def _lock_plant(plant):
    """Reload one plant under a row lock, serialising its transitions."""
    return SpecificPlant.objects.select_for_update().get(pk=plant.pk)


def _plant_events(plant):
    """Return every recorded fact for one plant."""
    return list(PlantLifecycleEvent.objects.filter(plant=plant))


def _plant_batch(plant):
    """Return the batch that raised this plant."""
    if plant.batch_id:
        return plant.batch
    return plant.cell_planting.seed_tray_planting.batch


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def _article_for(word):
    """Return the indefinite article that reads correctly before a word."""
    return 'An' if word[:1].lower() in 'aeiou' else 'A'


def _require_transition(state, event_type):
    """Reject a fact that the plant's current condition does not permit."""
    if state not in ALLOWED_FROM.get(event_type, set()):
        described = LifecycleState(state).label.lower()
        raise ValidationError({
            'event_type': (
                f'{_article_for(described)} {described} plant cannot be '
                f'recorded as {EventType(event_type).label.lower()}.'
            ),
        })


def _require_chronology(events, occurred_at):
    """Keep the history append-only in time as well as in storage."""
    latest = max((event.occurred_at for event in events), default=None)
    if latest is not None and occurred_at < latest:
        raise ValidationError({
            'occurred_at': 'Events must be recorded in the order they happened.',
        })


def validate_outcome(plant, event_type, occurred_at, reason=''):
    """Check one plant admits a fact before anything is written.

    The reason defaults to none so that a caller which forgets to pass one
    refuses a backward fact rather than recording an unexplained withdrawal.
    """
    if event_type in BACKWARD_EVENTS:
        _require_reason(reason)
    events = _plant_events(plant)
    _require_chronology(events, occurred_at)
    _require_transition(derive_state(events).state, event_type)


def _close_active_location(plant, when):
    """End the open location a departing or finished plant leaves behind."""
    locations = list(
        SpecificPlantLocation.objects
        .select_for_update()
        .filter(specific_plant=plant, ended__isnull=True)
    )
    if len(locations) > 1:
        raise ValidationError({
            'detail': 'The plant has multiple active locations.',
        })
    if not locations:
        return None
    location = locations[0]
    if when < location.started:
        raise ValidationError({
            'occurred_at': 'An outcome cannot predate the current location.',
        })
    location.ended = when
    location.save(update_fields=['ended'])
    return location


def _create_event(plant, user, request, reversal_of=None):
    """Append one immutable fact, denormalising the batch that raised it."""
    return PlantLifecycleEvent.objects.create(
        workspace=plant.workspace,
        plant=plant,
        batch=_plant_batch(plant),
        event_type=request.event_type,
        occurred_at=request.occurred_at,
        reason=request.reason,
        reference=request.reference,
        reversal_of=reversal_of,
        created_by=user if user is not None and user.is_authenticated else None,
    )


def _apply_outcome(plant, user, request):
    """Close a location where required and append the outcome fact."""
    if request.event_type in CLOSES_LOCATION:
        _close_active_location(plant, request.occurred_at)
    return _create_event(plant, user, request)


@transaction.atomic
def record_lifecycle_event(plant, user, request):
    """Record one validated fact about a plant and close its location if final."""
    plant = _lock_plant(plant)
    request = request.at(timezone.now())
    validate_outcome(plant, request.event_type, request.occurred_at, request.reason)
    return _apply_outcome(plant, user, request)


def record_germination_event(plant, user):
    """Record the germination that created this plant.

    Called from inside the transaction that creates the plant, which already
    holds the locks the fact depends on.
    """
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.GERMINATED, occurred_at=plant.germinated),
    )


def record_transplant_event(plant, user, occurred_at):
    """Record that a move planted this plant out.

    Called from inside `move_specific_plant`, which already locks the plant.
    """
    validate_outcome(plant, EventType.TRANSPLANTED, occurred_at)
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.TRANSPLANTED, occurred_at=occurred_at),
    )


@transaction.atomic
def reverse_lifecycle_event(event, user, reason, occurred_at=None):
    """Correct a mistaken fact by appending its reversal.

    The original stays visible; the plant's state is re-derived from the facts
    that survive. A closed location is not reopened, because where a plant has
    been remains true — record the replacement location instead.
    """
    _require_reason(reason)
    plant = _lock_plant(event.plant)
    event = PlantLifecycleEvent.objects.get(pk=event.pk)
    if event.event_type == EventType.CORRECTED:
        raise ValidationError({'event': 'A correction cannot itself be corrected.'})
    if event.event_type == EventType.GERMINATED:
        raise ValidationError({
            'event': 'Germination created this plant and cannot be reversed.',
        })
    if hasattr(event, 'reversal'):
        raise ValidationError({'event': 'That event has already been corrected.'})
    occurred_at = occurred_at or timezone.now()
    _require_chronology(_plant_events(plant), occurred_at)
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.CORRECTED, occurred_at=occurred_at, reason=reason),
        reversal_of=event,
    )


@transaction.atomic
def record_bulk_outcome(plant_ids, user, request):
    """Record the same outcome for a selection as one event per plant.

    Every plant is validated before anything is written, so an invalid
    selection reports each offending plant without half-applying the batch.
    """
    wanted = sorted(set(plant_ids))
    if not wanted:
        raise ValidationError({'plants': 'Select at least one plant.'})
    plants = list(
        SpecificPlant.objects
        .select_for_update()
        .filter(pk__in=wanted)
        .order_by('pk')
    )
    if len(plants) != len(wanted):
        raise ValidationError({'plants': 'One or more plants are unavailable.'})

    request = request.at(timezone.now())
    errors = []
    for plant in plants:
        try:
            validate_outcome(
                plant, request.event_type, request.occurred_at, request.reason,
            )
        except ValidationError as exc:
            errors.append(f'Plant {plant.pk}: {" ".join(exc.messages)}')
    if errors:
        raise ValidationError({'plants': errors})

    return [_apply_outcome(plant, user, request) for plant in plants]
