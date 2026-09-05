"""One chronological reading of everything recorded about a plant.

A plant's history is written in four places, each owned by a different part of
the operation. `PlantLifecycleEvent` says what became of it, `NurseryObservation`
says what was done to it, `health.QuarantineAction` says when it was held out of
reach for disease, and the sales allocation with its `ReservationEvent`s says who
it was promised to. All four are append-only and all four are authoritative;
what was missing was anything that read them together, so an operator asking
"what happened to this plant" had four screens open and interleaved the dates by
hand — and the causal sequence they were looking for, potted on, graded down,
quarantined, failed, existed in the data and nowhere on the screen.

This is a projection, in the same spirit as the plant register: it reads the
existing rows and writes nothing. There is no timeline table, so there is
nothing to synchronise and nothing that can drift away from its sources.

Two rules make the reading trustworthy.

Corrections stay visible. Every source expresses a correction by appending —
a reversing lifecycle event, a replacement observation — and a view that
silently dropped the struck fact would misrepresent what the operator saw at
the time. A superseded entry is marked `corrected` and the entry that struck it
names its target in `corrects`; neither is hidden.

Ordering is one rule across every source: `(occurred_at, source rank, pk)`.
The timestamps are comparable, the primary keys are not — they come from
different tables — so the source rank settles a tie before the key is reached,
and `SOURCE_ORDER` states that rank once rather than leaving each caller to
guess. It follows the `(occurred_at, pk)` convention `derive_state` already
replays a lifecycle with.
"""

from typing import NamedTuple

from django.db import models

from health.models import QuarantineAction
from locations.models import Location, location_full_name
from sales.models import ReservationEvent, SalesOrderAllocation

from .models import (
    CohortEvent,
    CohortOperation,
    NurseryObservation,
    PlantLifecycleEvent,
)


#: Where each entry was read from. `allocation` and `reservation` are separate
#: because the commercial claim is written in two tables: the allocation row is
#: the promise itself, and its reservation events are what became of it.
class Source(models.TextChoices):
    """The append-only record each timeline entry was projected from.

    A vocabulary rather than six loose strings, so a screen naming the four
    histories and a report filtering on one of them cannot disagree about what
    they are called.
    """

    COHORT = 'cohort', 'Block history'
    LIFECYCLE = 'lifecycle', 'Lifecycle'
    OBSERVATION = 'observation', 'Nursery observation'
    QUARANTINE = 'quarantine', 'Health'
    ALLOCATION = 'allocation', 'Order promise'
    RESERVATION = 'reservation', 'Reservation'


#: The tie-break when two sources report the same instant, which they routinely
#: do: a promotion writes a cohort operation, a lifecycle event and an
#: observation inside one transaction at one time. Cohort history sorts first
#: because it is what the plant came out of, and a promise sorts before what
#: became of it.
SOURCE_ORDER = {
    Source.COHORT: 0,
    Source.LIFECYCLE: 1,
    Source.OBSERVATION: 2,
    Source.QUARANTINE: 3,
    Source.ALLOCATION: 4,
    Source.RESERVATION: 5,
}

#: The kind recorded for the allocation row itself. The reservation events use
#: their own vocabulary, so this one word is all that is invented here.
PROMISED = 'promised'


class TimelineEntry(NamedTuple):
    """One dated fact about a plant, projected from one source row.

    `entry_id` is stable across requests and unique across sources, so a screen
    can key a list on it and `corrects` can name another entry without the
    reader having to know which table either came from.
    """

    source: str
    kind: str
    label: str
    occurred_at: object
    source_id: int
    summary: str
    detail: dict
    corrected: bool = False
    corrects: object = None

    @property
    def entry_id(self):
        """Return this entry's stable identity across the whole timeline."""
        return entry_id(self.source, self.kind, self.source_id)

    def as_dict(self):
        """Return the JSON-safe representation the API and reports share."""
        return {
            'entry_id': self.entry_id,
            'source': str(self.source),
            'kind': self.kind,
            'label': self.label,
            'occurred_at': self.occurred_at,
            'source_id': self.source_id,
            'summary': self.summary,
            'detail': self.detail,
            'corrected': self.corrected,
            'corrects': self.corrects,
        }


def entry_id(source, kind, source_id):
    """Name one entry uniquely, including the kind so two rows cannot collide.

    An allocation and one of its reservation events can share a primary key,
    and so can a lifecycle event and an observation, so the source alone is not
    enough to tell two entries apart.
    """
    return f'{source}:{kind}:{source_id}'


def _order(entry):
    """Return the sort key stated in this module's docstring."""
    return (entry.occurred_at, SOURCE_ORDER[entry.source], entry.source_id)


def _location_names(locations):
    """Return one pk-to-name map covering every named location's ancestry.

    `location_full_name` costs a query per location without it, and a block
    moved a dozen times would pay that a dozen times over for names it has
    already read.
    """
    ancestors = {
        pk for location in locations for pk in location.ancestor_ids
    }
    ancestors.update(location.pk for location in locations)
    return dict(
        Location.objects.filter(pk__in=ancestors).values_list('pk', 'name'),
    )


def _named(location, names):
    """Return a location's full name, or None where there is no location."""
    return None if location is None else location_full_name(location, names)


def _decimal(value):
    """Render a measured value as the string the decimal column stores.

    Parsing it to a float here would reintroduce exactly the artifacts the
    decimal column exists to avoid, and the browser formats the padded string
    losslessly.
    """
    return None if value is None else str(value)


def _lifecycle_entries(plant):
    """Project what became of the plant, corrections included.

    Every event type is carried, not only the state-changing ones `derive_state`
    replays: `transplanted` changes no condition and is one of the facts a
    reader is looking for.
    """
    events = plant.lifecycle_events.select_related('reversal', 'reversal_of', 'created_by')
    entries = []
    for event in events:
        reversal = getattr(event, 'reversal', None)
        entries.append(TimelineEntry(
            source=Source.LIFECYCLE,
            kind=event.event_type,
            label=PlantLifecycleEvent.EventType(event.event_type).label,
            occurred_at=event.occurred_at,
            source_id=event.pk,
            summary=event.reason or PlantLifecycleEvent.EventType(event.event_type).label,
            detail={
                'reason': event.reason,
                'reference': event.reference,
                'recorded_by': getattr(event.created_by, 'username', None),
            },
            corrected=reversal is not None,
            corrects=(
                None if event.reversal_of_id is None
                else entry_id(
                    Source.LIFECYCLE,
                    event.reversal_of.event_type,
                    event.reversal_of_id,
                )
            ),
        ))
    return entries


#: How an observation is named, most significant fact first. One observation is
#: one dated fact-set — potting on and grading down are routinely recorded
#: together — so the kind names what an operator would call the entry and
#: `detail` carries the rest rather than the row being split into several.
_OBSERVATION_KINDS = (
    ('container_item_id', 'potted_on', 'Potted on'),
    ('stage_id', 'staged', 'Growth stage recorded'),
    ('grade_id', 'graded', 'Graded'),
    ('height_cm', 'measured', 'Measured'),
    ('spread_cm', 'measured', 'Measured'),
    ('root_condition', 'roots_checked', 'Roots checked'),
    ('expected_ready', 'expected_ready', 'Expected ready date recorded'),
    ('photo_url', 'photographed', 'Photographed'),
)


def _observation_kind(observation):
    """Return the kind and label naming what this observation mainly records."""
    for field, kind, label in _OBSERVATION_KINDS:
        if getattr(observation, field) not in (None, ''):
            return kind, label
    return 'noted', 'Note recorded'


def _observation_summary(observation):
    """Describe one observation in the words its own catalogs supply."""
    parts = [
        observation.stage.name if observation.stage_id else None,
        observation.grade.name if observation.grade_id else None,
        (
            f'{observation.container_name} {observation.container_size_label}'.strip()
            if observation.container_item_id else None
        ),
        observation.root_condition or None,
        observation.notes or None,
    ]
    return ' · '.join(part for part in parts if part)


def _observation_entries(queryset, scope, cohort_id=None):
    """Project cultivation facts, corrected ones marked rather than dropped."""
    observations = queryset.select_related(
        'stage', 'grade', 'container_item', 'corrects', 'correction', 'created_by',
    )
    entries = []
    for observation in observations:
        kind, label = _observation_kind(observation)
        entries.append(TimelineEntry(
            source=Source.OBSERVATION,
            kind=kind,
            label=label,
            occurred_at=observation.occurred_at,
            source_id=observation.pk,
            summary=_observation_summary(observation),
            detail={
                'scope': scope,
                'cohort': cohort_id,
                'stage': observation.stage.name if observation.stage_id else None,
                'grade': observation.grade.name if observation.grade_id else None,
                'container': observation.container_name or None,
                'container_size': observation.container_size_label or None,
                'container_count': observation.container_count,
                'height_cm': _decimal(observation.height_cm),
                'spread_cm': _decimal(observation.spread_cm),
                'root_condition': observation.root_condition,
                'expected_ready': observation.expected_ready,
                'photo_url': observation.photo_url,
                'notes': observation.notes,
                'recorded_by': getattr(observation.created_by, 'username', None),
            },
            corrected=hasattr(observation, 'correction'),
            corrects=(
                None if observation.corrects_id is None
                else entry_id(
                    Source.OBSERVATION,
                    _observation_kind(observation.corrects)[0],
                    observation.corrects_id,
                )
            ),
        ))
    return entries


def _quarantine_entries(plant):
    """Project the health overlay as the dated case actions that produced it.

    Membership itself carries no time of its own, so the case's actions are the
    facts: the quarantine that constrained this plant, any escalation, and the
    release or cull that ended it.
    """
    actions = list(QuarantineAction.objects.filter(
        case__members__plant=plant,
    ).select_related('case', 'destination', 'created_by').distinct())
    names = _location_names([
        action.destination for action in actions if action.destination_id
    ])
    return [
        TimelineEntry(
            source=Source.QUARANTINE,
            kind=action.action,
            label=QuarantineAction.Action(action.action).label,
            occurred_at=action.occurred_at,
            source_id=action.pk,
            summary=action.reason,
            detail={
                'case': action.case_id,
                'case_reason': action.case.reason,
                'reason': action.reason,
                'destination': _named(action.destination, names),
                'recorded_by': getattr(action.created_by, 'username', None),
            },
        )
        for action in actions
    ]


def _allocation_entries(plant):
    """Project the commercial claim: the promise, then what became of it.

    The allocation row is a fact in its own right and not only a status to
    annotate — a tentative claim on a quote never reaches a reservation event,
    and it is still the reason a plant is spoken for.
    """
    allocations = SalesOrderAllocation.objects.filter(
        plant=plant,
    ).select_related('line__order__customer').order_by('pk')
    entries = []
    for allocation in allocations:
        order = allocation.line.order
        reference = {
            'order': order.pk,
            'order_number': order.order_number,
            'customer': order.customer.name if order.customer_id else None,
            'allocation': allocation.pk,
        }
        entries.append(TimelineEntry(
            source=Source.ALLOCATION,
            kind=PROMISED,
            label='Promised to an order',
            occurred_at=allocation.created,
            source_id=allocation.pk,
            summary=f'Promised to {order.order_number}',
            detail={**reference, 'expires_at': allocation.expires_at},
        ))
        for event in allocation.events.select_related('created_by'):
            entries.append(TimelineEntry(
                source=Source.RESERVATION,
                kind=event.event_type,
                label=ReservationEvent.EventType(event.event_type).label,
                occurred_at=event.occurred_at,
                source_id=event.pk,
                summary=event.reason or ReservationEvent.EventType(event.event_type).label,
                detail={
                    **reference,
                    'reason': event.reason,
                    'recorded_by': getattr(event.created_by, 'username', None),
                },
            ))
    return entries


def promotion_moment(plant):
    """Return when this plant left the cohort that raised it, or None.

    A promotion names the plants it created in its operation payload, which is
    the only record that ties one plant to one promotion — a cohort may be
    promoted repeatedly. A plant whose promotion cannot be found falls back to
    its germination, which `promote_cohort` timestamps from the cohort.
    """
    if plant.promoted_from_cohort_id is None:
        return None
    operations = CohortOperation.objects.filter(
        workspace_id=plant.workspace_id,
        action=CohortOperation.Action.PROMOTE,
        events__cohort_id=plant.promoted_from_cohort_id,
    ).order_by('occurred_at', 'pk')
    for operation in operations:
        if plant.pk in (operation.payload or {}).get('plants', []):
            return operation.occurred_at
    return plant.germinated


def _cohort_entries(plant, boundary):
    """Project the block's history from before this plant had an identity.

    A promoted plant was real before it was individual, and dropping everything
    at the promotion boundary loses the batch's early life — the sowing it came
    out of, the losses the block took, the pot it was standing in. Everything
    the cohort recorded up to and including the moment this plant left it is
    part of this plant's history.
    """
    cohort_id = plant.promoted_from_cohort_id
    events = list(CohortEvent.objects.filter(
        cohort_id=cohort_id,
        operation__occurred_at__lte=boundary,
    ).select_related('operation', 'location_before', 'location_after'))
    names = _location_names([
        location for event in events
        for location in (event.location_before, event.location_after)
        if location is not None
    ])
    entries = [
        TimelineEntry(
            source=Source.COHORT,
            kind=event.operation.action,
            label=CohortOperation.Action(event.operation.action).label,
            occurred_at=event.operation.occurred_at,
            source_id=event.pk,
            summary=event.operation.reason,
            detail={
                'cohort': cohort_id,
                'operation': event.operation_id,
                'reason': event.operation.reason,
                'loss_cause': event.operation.loss_cause or None,
                'quantity_before': event.quantity_before,
                'quantity_delta': event.quantity_delta,
                'quantity_after': event.quantity_after,
                'state_before': event.state_before,
                'state_after': event.state_after,
                'location_before': _named(event.location_before, names),
                'location_after': _named(event.location_after, names),
            },
        )
        for event in events
    ]
    entries.extend(_observation_entries(
        NurseryObservation.objects.filter(
            targets__cohort_id=cohort_id,
            occurred_at__lte=boundary,
        ),
        scope='cohort',
        cohort_id=cohort_id,
    ))
    return entries


def plant_timeline(plant):
    """Return everything recorded about one plant, oldest first.

    The caller gets `TimelineEntry` rows rather than dictionaries so the API,
    the traceability report, and any future reader share one shape and one
    ordering rather than each assembling their own.
    """
    entries = _lifecycle_entries(plant)
    entries.extend(_observation_entries(
        NurseryObservation.objects.filter(targets__plant=plant), scope='plant',
    ))
    entries.extend(_quarantine_entries(plant))
    entries.extend(_allocation_entries(plant))
    boundary = promotion_moment(plant)
    if boundary is not None:
        entries.extend(_cohort_entries(plant, boundary))
    return sorted(entries, key=_order)


def timeline_rows(plant):
    """Return one plant's timeline as the JSON-safe rows callers serialise."""
    return [entry.as_dict() for entry in plant_timeline(plant)]
