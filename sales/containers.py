"""What happens to the plants riding inside a container that is sold.

A tray is lent to a crop and comes back, so it may not go out holding plants
nobody sold. A numbered pot is sold *with* what is growing in it, so its
plants come along, are returned as part of the same line, and — once they have
actually left — the pot stops being an asset and becomes one of their inputs.

That last step is deliberately not appended here. It reaches the plant through
`costing.sources.container_sources`, which derives it from the rider rows, so a
return or a reversal takes the cost back off on the next recalculation without
anything having to remember that it was ever put on.
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from costing.models import CostAllocationRun
from costing.services import reallocate_batches
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_lifecycle_event,
)
from plantings.models import ProductionBatch, SpecificPlantLocation

from .models import (
    FulfillmentRider,
    SalesOrderAllocation,
    SalesReturnLine,
)


def return_event(outcome):
    """Return the lifecycle event one physical return outcome records."""
    return {
        SalesReturnLine.Outcome.AVAILABLE: EventType.RETURNED_AVAILABLE,
        SalesReturnLine.Outcome.QUARANTINED: EventType.RETURNED_QUARANTINED,
        SalesReturnLine.Outcome.DISCARDED: EventType.RETURNED_DISCARDED,
    }[outcome]


def resolve_riders(units, selected_plant_ids):
    """Decide, per unit, what happens to the plants it is carrying.

    A tray is a container being lent, so it may not go out holding plants
    nobody sold: the fulfillment has to name them. A numbered pot is sold with
    what is growing in it, so its plants come along and are returned as part of
    the same line rather than needing a second one.
    """
    riders = {}
    for unit in units.values():
        try:
            tray = unit.seed_tray
        except ObjectDoesNotExist:
            riders[unit.pk] = list(SpecificPlantLocation.objects.filter(
                container_unit=unit, ended__isnull=True,
            ).select_related('specific_plant__batch'))
            continue
        carried = set(SpecificPlantLocation.objects.filter(
            seed_tray_cell__tray=tray, ended__isnull=True,
        ).values_list('specific_plant_id', flat=True))
        if not carried.issubset(selected_plant_ids):
            raise ValidationError({
                'allocations': f'Tray {tray.pk} still carries plants not in this fulfillment.',
            })
        riders[unit.pk] = []
    return riders


def validate_riders_are_free(riders, order):
    """Refuse to sell a pot whose plants are already promised elsewhere.

    A potted specimen is sellable exactly once. Without this the same plant
    could go out on its own seedling line and again inside its container.
    """
    plant_ids = [
        placement.specific_plant_id
        for placements in riders.values()
        for placement in placements
    ]
    if not plant_ids:
        return
    claimed = SalesOrderAllocation.objects.filter(
        plant_id__in=plant_ids,
        status__in=(
            SalesOrderAllocation.Status.RESERVED,
            SalesOrderAllocation.Status.FULFILLED,
        ),
    ).exclude(line__order=order).values_list('plant_id', flat=True)
    duplicated = sorted(set(claimed))
    if duplicated:
        raise ValidationError({
            'allocations': (
                f'Plants {duplicated} are promised on another order and cannot '
                'be sold inside a container.'
            ),
        })


def riders_of(fulfillment):
    """Return every plant that travelled inside one fulfillment's containers."""
    return FulfillmentRider.objects.filter(
        fulfillment_line__fulfillment=fulfillment,
    ).select_related('plant')


def recost_container_plants(plants, user, reason):
    """Recalculate the batches whose plants just changed hands inside a pot.

    The container's cost reaches the plant through
    `costing.sources.container_sources`, which derives it from the rider rows
    rather than appending a layer here. That is what lets a return, or a
    reversal, take the cost back off on the next recalculation without anything
    having to remember that it was ever put on.

    Always called after a fulfillment line's own cost of sale is snapshotted,
    never before: the pot is already on that line through its acquisition cost,
    and posting the layer first would have `_plant_cost` count it again inside
    the riders.
    """
    if not plants:
        return []
    batches = list(ProductionBatch.objects.filter(
        pk__in={plant.batch_id for plant in plants},
    ))
    return reallocate_batches(
        batches, user, CostAllocationRun.Trigger.CONTAINER_SOLD, reason,
    )


def sell_rider(line, placement, user, fulfilled_at, cogs_amount):
    """Record one plant as sold because the container holding it was.

    The plant's placement ends here rather than following the pot: once the
    container has left, saying the plant is still standing in it would be a
    claim about somebody else's greenhouse.
    """
    plant = placement.specific_plant
    event = record_lifecycle_event(
        plant, user,
        OutcomeRequest(
            EventType.SOLD, occurred_at=fulfilled_at,
            reference=f'fulfillment:{line.fulfillment_id}:container:{line.pk}',
        ),
    )
    SpecificPlantLocation.objects.filter(pk=placement.pk).update(ended=fulfilled_at)
    return FulfillmentRider.objects.create(
        fulfillment_line=line,
        plant=plant,
        lifecycle_event=event,
        cogs_amount=cogs_amount,
    )


def return_riders(line, sales_return, user, outcome):
    """Bring a returned container's plants back with it.

    They went out as passengers on this line, so they come back on it too,
    landing in the pot again unless it is being discarded. Returns the plants
    needing quarantine, which the caller handles for the whole document at
    once.

    The document supplies the time and the reason rather than the caller
    repeating them: these plants came back because it says they did, and two
    copies of that could disagree.
    """
    returned_at = sales_return.returned_at
    reason = sales_return.reason
    quarantined = []
    for rider in line.riders.select_related('plant').all():
        event = record_lifecycle_event(
            rider.plant, user,
            OutcomeRequest(
                return_event(outcome), occurred_at=returned_at, reason=reason,
                reference=f'return:{sales_return.pk}:container:{line.pk}',
            ),
        )
        FulfillmentRider.objects.filter(pk=rider.pk).update(return_event=event)
        if outcome != SalesReturnLine.Outcome.DISCARDED:
            SpecificPlantLocation.objects.create(
                specific_plant=rider.plant,
                location_type=SpecificPlantLocation.CONTAINER_UNIT,
                container_unit=line.allocation.inventory_unit,
                started=returned_at,
                notes=reason,
            )
        if outcome == SalesReturnLine.Outcome.QUARANTINED:
            quarantined.append(rider.plant)
    return quarantined
