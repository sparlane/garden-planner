"""The one mapping between the cohort and individual-plant loss vocabularies.

The same nursery event is recorded two ways depending on whether the stock has
identities. An individual plant gets a `PlantLifecycleEvent`; anonymous stock
gets a `CohortOperation` with the `LOSS` action and a `LossCause`. A batch whose
cohort was promoted partway holds both, so anything totalling loss has to read
both, and must not need a human to interpret free text to do it.

Where the two sides correspond:

- `failed` / `failed` — it died. Points at growing conditions.
- `lost` / `lost` — not found at stocktake. Points at stock control.
- `culled` / `culled` — deliberately destroyed, the quarantine cull included.
  Points at grading standards or disease policy.
- `donated` / `donated` — given away. It leaves production without revenue,
  which is why `costing` already treats it as production loss.

Where a plant event has no cohort cause:

- `sold` and `harvest_finished` are not losses; they resolve stock into revenue
  or a crop.
- `germinated`, `ready`, `transplanted`, `retained`, `held_back` and
  `retention_ended` record condition, not a departure from stock. The cohort
  side says the same things through `observe`, `ready`, `retain`, `move` and the
  growth observations.
- `returned_available`, `returned_quarantined`, `returned_discarded`,
  `released_available` and `corrected` all act on a plant somebody was sold or
  on one plant's history. Anonymous stock is not sold as a nameable plant, so
  there is nothing to return or release one unit of; `health` and the
  append-only operation history carry the cohort equivalents.

Where a cohort cause has no plant event:

- `unspecified` is history only. Losses recorded before a cause was required
  were backfilled to it rather than guessed from their reason text, and
  `CohortOperation.clean` refuses it on a new loss.

The cohort actions `observe`, `adjust`, `split`, `merge` and `promote` are about
anonymous quantity rather than loss, and have no individual counterpart on
purpose.

Ungerminated seed borrows the cause vocabulary without joining these totals.
`SowingGerminationClosure` records the seed a closed sowing never turned into a
seedling, with a cause from `RECORDABLE_CAUSES`, so loss by cause reads the same
whether the thing lost had an identity, a cohort, or never came up at all. It
stays out of `loss_by_cause` because it is counted in seeds: a seed that never
germinated was never a unit of stock, so adding it to a plant total would make
the production report's loss equation stop reconciling against the plants and
cohort units it is derived from. `reporting.germination` totals it separately,
and its cost reaches production loss through `costing.allocation.retire_ungerminated`
rather than through anything here.

Adding an event or a cause to either side means adding it here, or saying here
why it has no counterpart.
"""

from collections import Counter

from django.db.models import Count, Sum

from .lifecycle import EventType
from .models import CohortEvent, CohortOperation, PlantLifecycleEvent


LossCause = CohortOperation.LossCause

#: The cohort cause each individual-plant loss event corresponds to.
CAUSE_OF_EVENT = {
    EventType.FAILED: LossCause.FAILED,
    EventType.LOST: LossCause.LOST,
    EventType.CULLED: LossCause.CULLED,
    EventType.DONATED: LossCause.DONATED,
}

#: The plant event each recordable cohort cause corresponds to.
EVENT_OF_CAUSE = {cause: event for event, cause in CAUSE_OF_EVENT.items()}

#: The plant events that remove a unit from production.
LOSS_EVENTS = frozenset(CAUSE_OF_EVENT)

#: The causes an operator may record now, in the order screens offer them.
RECORDABLE_CAUSES = tuple(EVENT_OF_CAUSE)

#: Every cause a total can be grouped by. `unspecified` is last because it is
#: the residue of history rather than a finding about the crop.
LOSS_CAUSES = RECORDABLE_CAUSES + (LossCause.UNSPECIFIED,)


def empty_totals():
    """Return a zero for every cause, so a total never omits an absent one."""
    return {cause.value: 0 for cause in LOSS_CAUSES}


def plant_loss_counts(events):
    """Count surviving plant loss events by the cohort cause they map to."""
    counts = Counter()
    rows = events.filter(
        event_type__in=LOSS_EVENTS, reversal__isnull=True,
    ).values('event_type').annotate(total=Count('pk'))
    for row in rows:
        counts[CAUSE_OF_EVENT[row['event_type']].value] += row['total']
    return counts


def cohort_loss_counts(cohort_events):
    """Sum lost cohort units by the cause recorded on their operation."""
    counts = Counter()
    rows = cohort_events.filter(
        operation__action=CohortOperation.Action.LOSS,
        quantity_delta__lt=0,
    ).values('operation__loss_cause').annotate(total=Sum('quantity_delta'))
    for row in rows:
        counts[row['operation__loss_cause']] += abs(row['total'])
    return counts


def loss_by_cause(*, plant_events=None, cohort_events=None):
    """Total lost units by cause across whichever populations are given.

    Both halves of a batch that was promoted partway answer in the same
    vocabulary, so their totals add rather than having to be read separately.
    """
    totals = empty_totals()
    if plant_events is not None:
        for cause, count in plant_loss_counts(plant_events).items():
            totals[cause] += count
    if cohort_events is not None:
        for cause, count in cohort_loss_counts(cohort_events).items():
            totals[cause] += count
    return totals


def batch_loss_by_cause(batch):
    """Total one batch's lost units by cause, anonymous and identified alike."""
    return loss_by_cause(
        plant_events=PlantLifecycleEvent.objects.filter(batch=batch),
        cohort_events=CohortEvent.objects.filter(cohort__batch=batch),
    )
