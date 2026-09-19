"""Append-only lifecycle history and derived state for individual plants.

Current state is replayed from `PlantLifecycleEvent` rows every time it is
asked for, so the APIs and the batch reports share one derivation and no stored
status can drift away from the recorded facts.
"""

# pylint: disable=duplicate-code

from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, Exists, F, OuterRef, Subquery, Value, When
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
    WITHDRAWN = 'withdrawn', 'Never observed'


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

#: States that resolve a plant: no outcome is owed for it any more. This is the
#: question the register's `unresolved` count, the batch reports and the loss
#: reconciliation ask. It is not whether the plant is still here — that is
#: `PRESENT_STATES`. Retained is final for availability without ending
#: biological growth, so failure or harvest may still follow it, and a retained
#: plant is still standing on a bench.
#:
#: `withdrawn` resolves a plant the other way: not by saying what became of it,
#: but by saying there was never one to become anything. It is final because
#: nothing can follow a seedling that did not come up, and it is the only state
#: no event in `STATE_AFTER` produces — `derive_state` reads it off the
#: correction that struck the germination out.
FINAL_STATES = {
    LifecycleState.RETAINED,
    LifecycleState.DONATED,
    LifecycleState.FAILED,
    LifecycleState.LOST,
    LifecycleState.CULLED,
    LifecycleState.HARVESTED,
    LifecycleState.SOLD,
    LifecycleState.DISCARDED,
    LifecycleState.WITHDRAWN,
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

#: States in which the nursery still physically holds a plant: the question a
#: health case, an input application or a nursery observation asks, where
#: `FINAL_STATES` asks whether an outcome is still owed. The two differ at
#: `retained`, which resolves a plant while leaving it on the bench.
#:
#: Derived rather than listed, on the precedent of `OFFERING_EVENTS`: a plant
#: with no history is growing, and every other state it can stand in is one some
#: fact leaves behind without closing its location. That puts a returned
#: quarantined plant here, because it is back on a bench, and leaves out a
#: discarded return, which was destroyed, and `withdrawn`, which was never
#: there. A state added to `STATE_AFTER` is classified by the fact that
#: produces it, so it cannot be forgotten here.
PRESENT_STATES = frozenset({
    LifecycleState.GROWING,
    *(
        state
        for event_type, state in STATE_AFTER.items()
        if event_type not in CLOSES_LOCATION
    ),
})

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
    state_since: object = None


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


def is_present(state):
    """Return whether a plant in this derived state is still physically held."""
    return state in PRESENT_STATES


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


def absent_states_with_exits(
        state_after=None, allowed_from=None, present_states=None, closes_location=None,
):
    """Return the states a fact may follow that are wrongly treated as gone.

    The reverse of `states_without_exits`. A state some fact can still be
    recorded from describes a plant somebody can still act on, so it must be
    counted as physically present — otherwise the health workflow refuses to
    quarantine it, which is what `retained` was while `FINAL_STATES` answered
    both questions. The one legitimate exception is a state every fact reaches
    by closing the plant's location: `sold` admits the return facts, but those
    bring a plant back from a customer rather than act on one still here.
    A state with exits that no fact produces also counts as departed, because
    `all()` over no producing facts is true. Such a state is unreachable, so
    nothing is lost by not reporting it here.

    The vocabularies default to this module's, and are arguments so a proposed
    one can be checked before it ships.
    """
    state_after = STATE_AFTER if state_after is None else state_after
    allowed_from = ALLOWED_FROM if allowed_from is None else allowed_from
    present_states = PRESENT_STATES if present_states is None else present_states
    closes_location = CLOSES_LOCATION if closes_location is None else closes_location
    with_exits = {state for sources in allowed_from.values() for state in sources}
    departed = {
        state for state in with_exits
        if all(
            event_type in closes_location
            for event_type, after in state_after.items()
            if after == state
        )
    }
    return with_exits - set(present_states) - departed


def _surviving_state_events(events):
    """Return one plant's state-changing facts a correction has not struck.

    Reversed events and the corrections that reverse them are both dropped, so
    a correction restores whatever the surviving facts imply. Everything that
    replays a history starts here, so no two readings can disagree about which
    facts still count.
    """
    corrected_ids = {
        event.reversal_of_id
        for event in events
        if event.reversal_of_id is not None
    }
    return [
        event
        for event in sorted(events, key=lambda event: (event.occurred_at, event.pk))
        if event.pk not in corrected_ids and event.event_type in STATE_AFTER
    ]


def germination_correction(events):
    """Return the correction that struck this plant's germination out, or None.

    A plant exists because somebody recorded that it came up, so correcting
    that one fact does not leave a plant in an earlier state the way every
    other correction does — it leaves no plant. `derive_state` therefore reads
    this before replaying anything, and `_surviving_state_events` has already
    dropped both rows by the time the replay would reach them.
    """
    germination_ids = {
        event.pk
        for event in events
        if event.event_type == EventType.GERMINATED
    }
    return next(
        (
            event
            for event in sorted(events, key=lambda event: (event.occurred_at, event.pk))
            if event.reversal_of_id in germination_ids
        ),
        None,
    )


def derive_state(events):
    """Replay one plant's facts into its current summary."""
    withdrawal = germination_correction(events)
    if withdrawal is not None:
        return LifecycleSummary(
            state=LifecycleState.WITHDRAWN,
            sellable=False,
            final_outcome=EventType.CORRECTED,
            final_outcome_at=withdrawal.occurred_at,
            state_since=withdrawal.occurred_at,
        )
    state = LifecycleState.GROWING
    outcome = None
    outcome_at = None
    state_since = None
    for event in _surviving_state_events(events):
        state = STATE_AFTER[event.event_type]
        state_since = event.occurred_at
        if is_final(state):
            outcome, outcome_at = event.event_type, event.occurred_at
        else:
            outcome, outcome_at = None, None
    return LifecycleSummary(
        state=state,
        sellable=state in SELLABLE_STATES,
        final_outcome=outcome,
        final_outcome_at=outcome_at,
        state_since=state_since,
    )


class AvailabilityInterval(NamedTuple):
    """One span a plant spent offerable, open-ended while it still is."""

    started: object
    ended: object = None


def availability_intervals(events):
    """Return every span this plant was offerable, oldest first.

    A plant graded ready, held back, and graded ready again produces two
    spans, so a screen can report how long stock has actually been on offer
    rather than only the state the newest fact left behind. A correction
    strikes its target out of the replay, so a `ready` recorded in error
    leaves no span at all, while holding back leaves a closed one.
    """
    intervals = []
    started = None
    for event in _surviving_state_events(events):
        offerable = STATE_AFTER[event.event_type] in SELLABLE_STATES
        if offerable and started is None:
            started = event.occurred_at
        elif not offerable and started is not None:
            intervals.append(AvailabilityInterval(started, event.occurred_at))
            started = None
    if started is not None:
        intervals.append(AvailabilityInterval(started))
    return intervals


#: The facts `derive_state` reads. Every other event type records something that
#: happened without changing the condition it leaves behind.
STATE_EVENT_TYPES = tuple(STATE_AFTER)

#: The facts that put a plant on offer. Derived rather than listed, so a fact
#: added to `STATE_AFTER` with a sellable destination cannot be missed by the
#: first-offered date the register reports.
OFFERING_EVENTS = tuple(
    event_type
    for event_type, state in STATE_AFTER.items()
    if state in SELLABLE_STATES
)


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


def germination_withdrawals(plant_ref):
    """Return the corrections striking out one plant's germination.

    The database's reading of `germination_correction`, and the one predicate
    every count of observed seedlings is filtered by, so no caller can decide
    for itself what "this plant came up" means.
    """
    return (
        PlantLifecycleEvent.objects
        .filter(
            plant=plant_ref,
            event_type=EventType.CORRECTED,
            reversal_of__event_type=EventType.GERMINATED,
        )
        .order_by('occurred_at', 'pk')
    )


def observed_only(queryset):
    """Return the plants of `queryset` whose germination still stands.

    A withdrawn plant is not a plant that was lost, failed or was culled: those
    came up and then something happened to them, and the germination rate is
    right to keep counting them. This one never came up at all, so every figure
    derived from what a sowing produced has to drop it, and the cost it was
    holding goes back to the cell for the seedlings that did come up — or to
    the ungerminated remainder if none did.
    """
    return queryset.filter(~Exists(germination_withdrawals(OuterRef('pk'))))


def with_lifecycle_state(queryset):
    """Annotate derived lifecycle state onto a `SpecificPlant` queryset.

    The replay in `derive_state` keeps the last surviving state-changing fact,
    so taking the newest one here reaches the same answer while leaving the
    result filterable, sortable, countable, and pageable in the database. The
    two derivations share `STATE_AFTER`, `FINAL_STATES`, and `SELLABLE_STATES`
    so neither can quietly describe a different vocabulary from the other.

    `first_ready_at` is the oldest surviving offering fact, which is the start
    of the first span `availability_intervals` reports, and `last_state_at`
    says when the current state began. Between them a screen can tell stock
    held back yesterday from stock held back in March.
    """
    events = effective_state_events(OuterRef('pk'))
    offers = (
        PlantLifecycleEvent.objects
        .filter(
            plant=OuterRef('pk'),
            event_type__in=OFFERING_EVENTS,
            reversal__isnull=True,
        )
        .order_by('occurred_at', 'pk')
    )
    withdrawals = germination_withdrawals(OuterRef('pk'))
    return (
        queryset
        .annotate(
            last_state_event=Subquery(events.values('event_type')[:1]),
            recorded_state_at=Subquery(events.values('occurred_at')[:1]),
            withdrawn_at=Subquery(withdrawals.values('occurred_at')[:1]),
            first_ready_at=Subquery(offers.values('occurred_at')[:1]),
        )
        .annotate(
            # A withdrawal is checked before the replayed facts for the reason
            # `derive_state` checks it first: it strikes out the one fact that
            # brought the plant into being, so there is no earlier state left
            # for the replay to land on.
            lifecycle_state=Case(
                When(withdrawn_at__isnull=False, then=Value(LifecycleState.WITHDRAWN)),
                *[
                    When(last_state_event=event_type, then=Value(state))
                    for event_type, state in STATE_AFTER.items()
                ],
                default=Value(LifecycleState.GROWING),
                output_field=models.CharField(),
            ),
            last_state_at=Case(
                When(withdrawn_at__isnull=False, then=F('withdrawn_at')),
                default=F('recorded_state_at'),
                output_field=models.DateTimeField(null=True),
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
                When(withdrawn_at__isnull=False, then=Value(EventType.CORRECTED)),
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


def record_germination_event(plant, user, reason=''):
    """Record the germination that created this plant.

    Called from inside the transaction that creates the plant, which already
    holds the locks the fact depends on.

    The reason is empty for the ordinary case. It carries why a seedling was
    recorded after its sowing had been declared finished germinating, which
    `plantings.germination` requires and which belongs here rather than on the
    closure, because it is a fact about this plant.
    """
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.GERMINATED, occurred_at=plant.germinated, reason=reason),
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


def _reopen_closed_location(plant, event):
    """Reopen the placement a mistaken outcome closed, if nothing has followed it.

    The outcome ended the plant's latest placement at the moment it happened.
    If that placement is still the latest and still ends then, the plant never
    left it, so the departure is withdrawn with the fact that caused it. A
    placement in a pot fill stays closed: its departure has already fixed the
    fill's shares and posted the media cost, which a reopening cannot undo.
    """
    if event.event_type not in CLOSES_LOCATION:
        return None
    latest = (
        SpecificPlantLocation.objects
        .select_for_update()
        .filter(specific_plant=plant)
        .order_by('-started', '-pk')
        .first()
    )
    if latest is None or latest.ended != event.occurred_at or latest.container_fill_id:
        return None
    latest.ended = None
    latest.save(update_fields=['ended'])
    return latest


@transaction.atomic
def reverse_lifecycle_event(event, user, reason, occurred_at=None, restore_location=False):
    """Correct a mistaken fact by appending its reversal.

    The original stays visible; the plant's state is re-derived from the facts
    that survive. A closed location is not reopened by default, because the
    callers that undo a sale, a count or a tray clean put the plant back where
    it belongs themselves. An operator correcting a single mistaken outcome
    passes `restore_location`, so the plant is standing where it was and can be
    moved or repotted from there.

    This says the fact was never true. Where it was true and the situation
    then changed, record that instead: `BACKWARD_EVENTS` names the facts for
    it, and they leave both intervals in the history rather than erasing one.
    """
    _require_reason(reason)
    plant = _lock_plant(event.plant)
    event = PlantLifecycleEvent.objects.get(pk=event.pk)
    if event.event_type == EventType.CORRECTED:
        raise ValidationError({'event': 'A correction cannot itself be corrected.'})
    if event.event_type == EventType.GERMINATED:
        raise ValidationError({
            'event': (
                'Germination created this plant, so correcting it here would '
                'leave a plant with no beginning. Withdraw the germination '
                'instead.'
            ),
        })
    if hasattr(event, 'reversal'):
        raise ValidationError({'event': 'That event has already been corrected.'})
    occurred_at = occurred_at or timezone.now()
    _require_chronology(_plant_events(plant), occurred_at)
    if restore_location:
        _reopen_closed_location(plant, event)
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.CORRECTED, occurred_at=occurred_at, reason=reason),
        reversal_of=event,
    )


#: What a plant may still be attached to and have been imagined. Each of these
#: is either the germination's own paperwork or a ledger that reverses rather
#: than deletes, so none of them is somebody having treated the plant as real.
#:
#: Every other relation blocks, and the blocking set is derived from the model
#: rather than listed, so a relation added later denies the withdrawal until
#: somebody decides it belongs here. That is the safe default: admitting a new
#: way of using a plant would silently let a withdrawal strand it.
WITHDRAWAL_KEEPS = frozenset({
    'lifecycle_events',
    'locations',
    'bulk_operation_results',
    'cost_allocations',
})


def withdrawal_blocking_relations(keeps=None):
    """Return the relations whose rows deny that a plant was never observed."""
    keeps = WITHDRAWAL_KEEPS if keeps is None else keeps
    return tuple(sorted(
        relation.get_accessor_name()
        for relation in SpecificPlant._meta.related_objects
        if relation.get_accessor_name() not in keeps
    ))


def _require_withdrawable(plant):
    """Return the germination this plant may withdraw, or explain why it may not."""
    events = _plant_events(plant)
    germination = next(
        (event for event in events if event.event_type == EventType.GERMINATED),
        None,
    )
    if germination is None:
        raise ValidationError({
            'plant': 'No germination was ever recorded for this plant.',
        })
    if germination_correction(events) is not None:
        raise ValidationError({
            'plant': "This plant's germination has already been withdrawn.",
        })
    recorded = sorted({
        EventType(event.event_type).label.lower()
        for event in events
        if event.pk != germination.pk
    })
    if recorded:
        raise ValidationError({
            'plant': (
                'This plant has been worked on since it came up '
                f'({", ".join(recorded)}), so its germination is not the only '
                'thing that would have to be untrue. Record what actually '
                'happened to it instead.'
            ),
        })
    # Moving a plant records no lifecycle event, so the events alone would let
    # a seedling somebody carried to a garden square be called imaginary. The
    # one placement germination itself created is the only one allowed, and it
    # has to be the one the plant is still standing in.
    locations = list(plant.locations.all())
    if len(locations) > 1 or any(location.ended is not None for location in locations):
        raise ValidationError({
            'plant': (
                'This plant has been moved since it came up, so somebody '
                'handled it. Record what actually happened to it instead.'
            ),
        })
    attached = [
        name for name in withdrawal_blocking_relations()
        if getattr(plant, name).exists()
    ]
    if attached:
        raise ValidationError({
            'plant': (
                'This plant is referred to by records that assume it existed '
                f'({", ".join(attached)}). Resolve those first.'
            ),
        })
    return germination


def _withdraw_one(plant, user, reason, occurred_at):
    """Append one plant's withdrawal, leaving its batch to the caller."""
    germination = _require_withdrawable(plant)
    _require_chronology(_plant_events(plant), occurred_at)
    _close_active_location(plant, occurred_at)
    return _create_event(
        plant,
        user,
        OutcomeRequest(EventType.CORRECTED, occurred_at=occurred_at, reason=reason),
        reversal_of=germination,
    )


@transaction.atomic
def withdraw_germination(plant, user, reason, occurred_at=None):
    """Withdraw a germination that was recorded but never happened.

    This is the correction for a seedling that was entered twice, or entered
    against a tray the operator was not looking at. It is not the way to record
    one that came up and then died: that is `failed`, and the germination rate
    is right to keep counting it.

    The plant row stays, because the audit that created it and the cost layers
    that named it are both immutable and both still refer to it. What changes
    is that every figure derived from what came up stops counting it, the cost
    it was holding is reallocated back to its cell, and its state reads
    `withdrawn` rather than a seedling growing somewhere nobody can find.
    """
    return withdraw_germinations([plant.pk], user, reason, occurred_at)[0]


@transaction.atomic
def withdraw_germinations(plant_ids, user, reason, occurred_at=None):
    """Withdraw a selection of germinations as one all-or-nothing correction.

    The tray screen's pagination bug put whole fills in twice, so a selection
    rather than a plant is the unit an operator actually has to correct. Every
    plant is withdrawn or none is, for the reason the bulk outcomes are: half a
    correction leaves a germination figure that is wrong in a new way.

    Each affected batch is locked once, in key order, and reallocated once
    after the last withdrawal, so a hundred and forty-four corrections cost one
    reallocation rather than a hundred and forty-four of them.
    """
    # Withdrawal calls back into costing, which reads germination balances.
    from costing.models import CostAllocationRun  # pylint: disable=import-outside-toplevel,cyclic-import
    from costing.services import reallocate_batches  # pylint: disable=import-outside-toplevel,cyclic-import
    from .batches import lock_batch_with_plants  # pylint: disable=import-outside-toplevel,cyclic-import

    _require_reason(reason)
    wanted = sorted(set(plant_ids))
    if not wanted:
        raise ValidationError({'plants': 'Select at least one plant.'})
    # Read unlocked first, only to learn which batches are involved. Locking
    # the selection here instead would take the plants a caller happens to
    # name and then reach for the rest through `lock_batch_with_plants`, which
    # is the subset deadlock that function exists to avoid.
    batches = {}
    for plant in SpecificPlant.objects.filter(pk__in=wanted).order_by('pk'):
        batch = _plant_batch(plant)
        batches[batch.pk] = batch
    locked = [lock_batch_with_plants(batches[key]) for key in sorted(batches)]
    plants = list(
        SpecificPlant.objects.filter(pk__in=wanted).order_by('pk')
    )
    if len(plants) != len(wanted):
        raise ValidationError({'plants': 'One or more plants are unavailable.'})
    occurred_at = occurred_at or timezone.now()
    corrections = [
        _withdraw_one(plant, user, reason, occurred_at)
        for plant in plants
    ]
    reallocate_batches(
        locked, user, CostAllocationRun.Trigger.GERMINATION_WITHDRAWN,
    )
    return corrections


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
