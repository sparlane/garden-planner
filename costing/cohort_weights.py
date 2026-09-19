"""Weigh a batch's anonymous units by the cost they carry, not only by count.

A unit of a cohort is worth the same as every other unit of it only while
every unit arrived carrying cost. A recount that finds more plants than the
register held adds units that carried nothing: nobody bought an input for
them that the batch has not already divided. Counting them as ordinary units
would re-divide the block's cost over them and over every unit that had
already left — a dispatched unit's cost of sale would fall after the fact,
exactly as a shortfall once made it rise (task 149).

So found units join the block at no cost, and only the units standing there
share their value with them: the block's per-unit weight falls, and whatever
leaves afterwards leaves at the weight it had then. That is a moving average,
and it depends on the order things happened in, so it is read by replaying
the batch's cohort history. A unit's weight is measured against a block
nothing was ever found in, where every unit weighs one.

A count adjustment downward is no longer recorded here as a bare adjustment:
`plantings.cohorts.change_cohort` records the shortfall as a loss. The bare
adjustments downward that remain — recounts posted before task 149, and a
stocktake reversal taking back units its own count found — withdraw units
without taking cost with them, so what they held stays on the units still
standing. They are the mirror of a found unit, and one undoes the other.
"""

from decimal import Decimal
from typing import NamedTuple

from plantings.models import CohortEvent, CohortOperation

Action = CohortOperation.Action

#: The operations that pass units from one block to another.
TRANSFERS = (Action.SPLIT, Action.MERGE)


class Pool(NamedTuple):
    """A count of units and the weight they carry between them."""

    units: int
    weight: Decimal

    def add(self, units, weight):
        """Return this pool with `units` carrying `weight` added to it."""
        return Pool(self.units + units, self.weight + weight)

    def rate(self):
        """Return the weight of one unit, or None for an empty pool."""
        return self.weight / self.units if self.units else None


class CohortWeights(NamedTuple):
    """The per-unit weight of each kind of output, where it is not one."""

    standing: dict
    sold: dict
    lost: dict
    plants: dict

    def weigh(self, kind, target_id, units):
        """Return the weight of `units` units of one output kind."""
        if kind == 'plant':
            return self.plants.get(target_id, units)
        rate = getattr(self, KIND_RATES[kind]).get(target_id)
        return units if rate is None else units * rate


#: Which rate table weighs each cohort output kind.
KIND_RATES = {'cohort': 'standing', 'cohort_sale': 'sold', 'cohort_loss': 'lost'}

UNWEIGHTED = CohortWeights({}, {}, {}, {})


def _operations(batch):
    """Return the batch's cohort events grouped by operation, in the order recorded."""
    events = (
        CohortEvent.objects
        .filter(cohort__batch=batch)
        .exclude(quantity_delta=0)
        .select_related('operation')
        .order_by('pk')
    )
    grouped = {}
    for event in events:
        grouped.setdefault(event.operation_id, []).append(event)
    return grouped.values()


class _Replay:
    """The running state of one batch's blocks while their history is replayed."""

    def __init__(self):
        self.blocks = {}
        self.sold = {}
        self.lost = {}
        self.losses = {}
        self.plants = {}

    def block(self, event):
        """Return a block's pool, starting from its first recorded count.

        A block created before its history was recorded starts from the
        count its first event found, at full weight.
        """
        if event.cohort_id not in self.blocks:
            before = event.quantity_before
            self.blocks[event.cohort_id] = Pool(before, Decimal(before))
        return self.blocks[event.cohort_id]

    def take(self, event, units):
        """Take `units` out of a block at its current weight, and return that weight."""
        pool = self.block(event)
        rate = pool.rate()
        weight = Decimal(units) if rate is None else rate * units
        self.blocks[event.cohort_id] = pool.add(-units, -weight)
        return weight

    def put(self, event, units, weight):
        """Put `units` carrying `weight` into a block."""
        self.blocks[event.cohort_id] = self.block(event).add(units, weight)

    def withdraw(self, event, units):
        """Take units out of a block without their cost, as a bare adjustment does.

        Emptied that way, a block has nothing left for the cost to stand on,
        so its weight goes too and is shared out as it was before task 149.
        """
        pool = self.block(event).add(-units, Decimal('0'))
        self.blocks[event.cohort_id] = pool if pool.units else Pool(0, Decimal('0'))

    def transfer(self, events):
        """Move a split's or merge's units with the weight they carried."""
        moving = sum(
            (self.take(event, -event.quantity_delta) for event in events if event.quantity_delta < 0),
            Decimal('0'),
        )
        arriving = [event for event in events if event.quantity_delta > 0]
        units = sum(event.quantity_delta for event in arriving)
        for event in arriving:
            self.put(event, event.quantity_delta, moving * event.quantity_delta / units)

    def apply(self, operation, event):
        """Apply one single-block event."""
        units = abs(event.quantity_delta)
        action = operation.action
        if action == Action.ADJUST:
            if event.quantity_delta > 0:
                self.put(event, units, Decimal('0'))
            else:
                self.withdraw(event, units)
        elif action in (Action.LOSS, Action.SOLD, Action.PROMOTE):
            weight = self.take(event, units)
            if action == Action.LOSS:
                self.losses[operation.pk] = weight
                self._out(self.lost, event.cohort_id, units, weight)
            elif action == Action.SOLD:
                self._out(self.sold, event.cohort_id, units, weight)
            else:
                for plant_id in operation.payload.get('plants', []):
                    self.plants[plant_id] = weight / units
        elif action == Action.CORRECTED:
            weight = self.losses.get(operation.reversal_of_id, Decimal(units))
            self._out(self.lost, event.cohort_id, -units, -weight)
            self.put(event, units, weight)
        elif action == Action.RETURN:
            source = operation.payload.get('source', event.cohort_id)
            rate = self.sold.get(source, Pool(0, Decimal('0'))).rate() or Decimal('1')
            self._out(self.sold, source, -units, -rate * units)
            self.put(event, units, rate * units)
        else:
            self.put(event, event.quantity_delta, Decimal(event.quantity_delta))

    @staticmethod
    def _out(table, cohort_id, units, weight):
        table[cohort_id] = table.get(cohort_id, Pool(0, Decimal('0'))).add(units, weight)


def _rates(table):
    return {cohort_id: pool.rate() for cohort_id, pool in table.items() if pool.units > 0}


def cohort_weights(batch):
    """Return the per-unit weight of each of a batch's cohort outputs.

    A batch whose blocks were never recounted up or down by a bare adjustment
    has every unit at one, and is not replayed.
    """
    recounted = CohortEvent.objects.filter(
        cohort__batch=batch, operation__action=Action.ADJUST,
    ).exclude(quantity_delta=0)
    if not recounted.exists():
        return UNWEIGHTED
    replay = _Replay()
    for events in _operations(batch):
        operation = events[0].operation
        if operation.action in TRANSFERS:
            replay.transfer(events)
        else:
            for event in events:
                replay.apply(operation, event)
    return CohortWeights(
        standing=_rates(replay.blocks),
        sold=_rates(replay.sold),
        lost=_rates(replay.lost),
        plants=replay.plants,
    )
