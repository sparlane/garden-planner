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
from sales.models import FulfillmentLine, SalesOrderAllocation, SalesReturnLine

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


class _Sales:
    """Which order allocation each sale and return in a batch's history belongs to.

    A sold unit's weight is the one it left with, and a return has to give
    back exactly that weight, not the average of every sale out of the block:
    the sales that stay dispatched keep what they were charged. So each sale
    is weighed on its own, and a return finds the sale it undoes through the
    allocation both belong to. A dispatch and a customer return are linked to
    their events directly; a reversed dispatch and a reversed return name
    their document line in the operation's reference, as `sales.cohort_stock`
    writes it.

    What counts as sold is still the allocations that stand fulfilled, so a
    re-sale out of the block a return landed in counts against the cohort the
    allocation drew on, where `costing.sources.sold_cohort_quantities` counts it.
    """

    def __init__(self, batch):
        lines = FulfillmentLine.objects.filter(allocation__plant_cohort__batch=batch)
        returns = SalesReturnLine.objects.filter(fulfillment_line__allocation__plant_cohort__batch=batch)
        self.by_event = {}
        self.lines = {'fulfillment': {}, 'return': {}}
        for kind, rows in (
                ('fulfillment', lines.values_list('pk', 'cohort_event_id', 'allocation_id')),
                ('return', returns.values_list('pk', 'cohort_event_id', 'fulfillment_line__allocation_id'))):
            for line_id, event_id, allocation_id in rows:
                self.lines[kind][line_id] = allocation_id
                if event_id is not None:
                    self.by_event[event_id] = allocation_id
        self.batch = batch

    def fulfilled(self):
        """Return each allocation standing fulfilled, with its cohort and quantity."""
        return (
            SalesOrderAllocation.objects
            .filter(plant_cohort__batch=self.batch, status=SalesOrderAllocation.Status.FULFILLED)
            .values_list('pk', 'plant_cohort_id', 'quantity')
        )

    def allocation_of(self, operation, event):
        """Return the allocation a sale or return moved units for, if it is known."""
        if event.pk in self.by_event:
            return self.by_event[event.pk]
        parts = str(operation.payload.get('reference', '')).split(':')
        if len(parts) == 4 and parts[2] == 'line' and parts[3].isdigit():
            return self.lines.get(parts[0], {}).get(int(parts[3]))
        return None


class _Replay:
    """The running state of one batch's blocks while their history is replayed."""

    def __init__(self, sales):
        self.blocks = {}
        self.sales = sales
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

    def returned(self, operation, event, units):
        """Give back the weight the sale this return undoes took, and return it.

        A sale nothing links it to comes back at full weight.
        """
        allocation = self.sales.allocation_of(operation, event)
        sold = self.sold.get(allocation)
        if sold is None or not sold.units:
            return Decimal(units)
        weight = sold.weight * min(units, sold.units) / sold.units
        self.sold[allocation] = sold.add(-min(units, sold.units), -weight)
        return weight + max(units - sold.units, 0)

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
                allocation = self.sales.allocation_of(operation, event)
                if allocation is not None:
                    self.sold[allocation] = self.sold.get(allocation, EMPTY).add(units, weight)
            else:
                for plant_id in operation.payload.get('plants', []):
                    self.plants[plant_id] = weight / units
        elif action == Action.CORRECTED:
            weight = self.losses.get(operation.reversal_of_id, Decimal(units))
            self._out(self.lost, event.cohort_id, -units, -weight)
            self.put(event, units, weight)
        elif action == Action.RETURN:
            self.put(event, units, self.returned(operation, event, units))
        else:
            self.put(event, event.quantity_delta, Decimal(event.quantity_delta))

    def sold_rates(self):
        """Return each cohort's weight per unit over the allocations still fulfilled."""
        pools = {}
        for allocation, cohort_id, quantity in self.sales.fulfilled():
            units = int(quantity)
            sold = self.sold.get(allocation)
            weight = Decimal(units) if sold is None or not sold.units else sold.weight * units / sold.units
            self._out(pools, cohort_id, units, weight)
        return _rates(pools)

    @staticmethod
    def _out(table, cohort_id, units, weight):
        table[cohort_id] = table.get(cohort_id, EMPTY).add(units, weight)


EMPTY = Pool(0, Decimal('0'))


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
    replay = _Replay(_Sales(batch))
    for events in _operations(batch):
        operation = events[0].operation
        if operation.action in TRANSFERS:
            replay.transfer(events)
        else:
            for event in events:
                replay.apply(operation, event)
    return CohortWeights(
        standing=_rates(replay.blocks),
        sold=replay.sold_rates(),
        lost=_rates(replay.lost),
        plants=replay.plants,
    )
