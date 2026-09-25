"""Dispatch and return the pot owned by a directly sold plant's fill."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.ledger import MovementRequest, UnitMovementRequest, post_stock_movement, post_unit_movement, unpromised_bulk, distribute_exactly, quantize_money, lock_lots, lock_units
from inventory.models import StockMovement
from locations.models import Location
from locations.occupancy import capacity_chain
from plantings.models import SpecificPlantLocation, SpecificPlant, ProductionBatch
from plantings.movement import move_specific_plant
from seedtrays.container_fills import lock_pot_fills, open_counted_fill, open_numbered_fill
from seedtrays.models import SeedTrayGeneration, SeedTrayGenerationEvent
from seedtrays.pot_media import pot_fill_remaining_media

from .cost_of_sale import UNSTATEABLE, CostOfSale, combine_costs, unit_cost_of_sale
from .models import FulfillmentContainer, FulfillmentLine, SalesOrderAllocation, SalesReturnLine


def lock_container_commerce(workspace, lines, destinations=()):
    """Keep returns and corrections in plant, capacity, stock, then fill order."""
    lines = list(lines)
    dispatches = [line.container_dispatch for line in lines if hasattr(line, 'container_dispatch')]
    if not dispatches:
        return
    plant_ids = [line.allocation.plant_id for line in lines if line.allocation.plant_id]
    plants = SpecificPlant.objects.filter(workspace=workspace, pk__in=plant_ids)
    list(ProductionBatch.objects.select_for_update().filter(pk__in=plants.values('batch_id')).order_by('pk'))
    list(plants.select_for_update().order_by('pk'))
    limits = {row.pk for destination in destinations if destination is not None for row in capacity_chain(destination)}
    list(Location.objects.select_for_update().filter(pk__in=limits).order_by('pk'))
    lot_ids = [line.allocation.stock_lot_id for line in lines if line.allocation.stock_lot_id]
    lot_ids += [row.stock_movement.lot_id for row in dispatches]
    lock_lots(workspace, lot_ids)
    unit_ids = [line.allocation.inventory_unit_id for line in lines if line.allocation.inventory_unit_id]
    unit_ids += [row.stock_movement.unit_id for row in dispatches if row.stock_movement.unit_id]
    lock_units(workspace, unit_ids)


def selected_pots(order, allocations, selected):
    """Resolve explicit with-pot choices without inventing a numbered identity.

    A pot bought abroad used to be refused here, because its cost was folded
    into a line stored under the order's currency and there was nowhere to say
    otherwise. The dispatch now records the pot's own currency beside its own
    cost (task 157), so a nursery potting into imported pots can sell the stock
    it legitimately holds.
    """
    chosen = {row.pk: row for row in allocations if row.pk in selected and row.plant_id}
    if set(chosen) != set(selected):
        raise ValidationError({'container_allocations': 'Choose selected individual plant allocations only.'})
    placements = {row.specific_plant_id: row for row in SpecificPlantLocation.objects.filter(
        specific_plant_id__in=[row.plant_id for row in chosen.values()], ended__isnull=True,
    ).select_related('container_fill__stock_lot', 'container_unit__source_lot')}
    result = {}
    for allocation in chosen.values():
        row = placements.get(allocation.plant_id)
        if row is None or row.container_fill_id is None or row.container_fill.tray_id:
            raise ValidationError({'container_allocations': 'With-pot dispatch requires a recorded pot fill.'})
        if row.container_unit_id:
            if SpecificPlantLocation.objects.filter(container_unit_id=row.container_unit_id, ended__isnull=True).exclude(specific_plant_id__in=[value.plant_id for value in chosen.values()]).exists():
                raise ValidationError({'container_allocations': 'Select every plant in the shared pot with its pot, or dispatch bare-root.'})
            if SalesOrderAllocation.objects.filter(inventory_unit_id=row.container_unit_id, status__in=['reserved', 'fulfilled']).exists():
                raise ValidationError({'container_allocations': 'This pot already has its own sales allocation.'})
        if row.container_fill.workspace_id != order.workspace_id:
            raise ValidationError({'container_allocations': 'The placement belongs to another workspace.'})
        result[allocation.plant_id] = row
    return result


def dispatch_pot(order, user, placement, fulfillment):
    """After the plant departs, send exactly the pot its recorded fill held.

    Returns the movement, the currency the pot was bought in, and what it cost.
    The currency is stated even where the cost is not: a lot or a unit names
    its currency whether or not anybody typed a price, and the pot's own record
    is the place that stays readable. `CostOfSale` keeps the stricter rule — no
    figure, no currency — because it is what a line's two columns store.

    The pot's currency is not always the order's: an imported pot holding a
    home-raised plant is two currencies on one line, and `sales.cost_of_sale`
    says what the line does with that.
    """
    fill, = lock_pot_fills(order.workspace, [placement.container_fill_id])
    when = fulfillment.fulfilled_at
    reference = f'fulfillment:{fulfillment.pk}:pot:{placement.pk}'
    if placement.container_unit_id:
        unit = placement.container_unit
        if fill.inventory_unit_id:
            close_dispatched_fill(fill, user, when, 'Dispatched with its plants.')
        movement = post_unit_movement(order.workspace, user, UnitMovementRequest(
            unit, StockMovement.MovementType.SALE, occurred_at=when, reason='Plant and pot dispatched.', reference=reference,
        ))
        return movement, unit.currency_code, unit_cost_of_sale(unit)
    lot = fill.stock_lot
    if unpromised_bulk(lot, fill.source_location) < 1:
        raise ValidationError({'container_allocations': 'The pot is no longer available for dispatch.'})
    movement = post_stock_movement(order.workspace, user, MovementRequest(
        lot, StockMovement.MovementType.SALE, Decimal('1'), source=fill.source_location,
        occurred_at=when, reason='Plant and pot dispatched.', reference=reference,
    ))
    return movement, lot.currency_code, CostOfSale(lot.base_unit_cost, lot.currency_code) if lot.base_unit_cost is not None else UNSTATEABLE


def close_dispatched_fill(fill, user, when, reason):
    """Retire an exhausted fill as dispatched, without inventing a clean."""
    if fill.plant_locations.filter(ended__isnull=True).exists() or pot_fill_remaining_media(fill):
        raise ValidationError({'container_allocations': 'The pot still holds plants or unallocated media.'})
    SeedTrayGeneration.objects.filter(pk=fill.pk).update(
        status=SeedTrayGeneration.Status.CLOSED, closed_at=when,
        close_reason=reason, closed_by=user, updated=timezone.now(),
    )
    SeedTrayGenerationEvent.objects.create(
        generation=fill, event_type=SeedTrayGenerationEvent.EventType.DISPATCHED,
        occurred_at=when, reason=reason, created_by=user,
    )


def dispatch_selected_pots(order, user, fulfillment, placements):  # pylint: disable=too-many-locals
    """Send each physical pot once after all its selected plants have departed.

    The pot's share lands on the line in the pot's own currency, and is added
    to what the plant cost only where the two agree. A home-raised plant in an
    imported pot leaves the line's cost of sale unstateable rather than adding
    a euro to a dollar — the pot's own row still says what it cost.
    """
    groups = {}
    lines = {line.allocation.plant_id: line for line in fulfillment.lines.select_related('allocation')}
    for row in placements.values():
        key = ('unit', row.container_unit_id) if row.container_unit_id else ('placement', row.pk)
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        group.sort(key=lambda row: row.specific_plant_id)
        movement, currency_code, pot = dispatch_pot(order, user, group[0], fulfillment)
        quantities = distribute_exactly(Decimal('1.000000000'), [Decimal('1')] * len(group), quantum=Decimal('0.000000001'))
        amounts = [None] * len(group) if pot.amount is None else distribute_exactly(quantize_money(pot.amount), [Decimal('1')] * len(group))
        for row, quantity, amount in zip(group, quantities, amounts):
            line = lines[row.specific_plant_id]
            FulfillmentContainer.objects.create(fulfillment_line=line, placement=row,
                                                stock_movement=movement, unit_cost=pot.amount, base_quantity=quantity,
                                                cogs_amount=amount, currency_code=currency_code)
            cogs = combine_costs([
                CostOfSale(line.cogs_amount, line.cogs_currency_code),
                CostOfSale(amount, currency_code) if amount is not None else UNSTATEABLE,
            ], order.currency_code)
            FulfillmentLine.objects.filter(pk=line.pk).update(
                cogs_amount=cogs.amount, cogs_currency_code=cogs.currency_code,
            )


def validate_pot_returns(items, lines):
    """A shared physical pot must return as a whole with one disposition."""
    values = {row['fulfillment_line'].pk: row for row in items}
    for line in lines.values():
        dispatch = getattr(line, 'container_dispatch', None)
        if dispatch is None:
            continue
        siblings = list(FulfillmentContainer.objects.filter(stock_movement=dispatch.stock_movement).values_list('fulfillment_line_id', flat=True))
        if any(pk not in values for pk in siblings):
            raise ValidationError({'items': 'Return every plant that shared this pot together.'})
        choices = {(values[pk]['outcome'], getattr(values[pk].get('destination'), 'pk', None)) for pk in siblings}
        if len(choices) != 1:
            raise ValidationError({'items': 'Plants sharing a pot must have the same return outcome and destination.'})


def return_pot(user, line, sales_return, item, recovered):  # pylint: disable=too-many-locals
    """Recover or discard the dispatched pot, never its already consumed media."""
    order = sales_return.order
    outcome, destination = item['outcome'], item.get('destination')
    dispatch = getattr(line, 'container_dispatch', None)
    if dispatch is None:
        return None, None, None
    original = dispatch.stock_movement
    if original.pk in recovered:
        movement, discard, fill = recovered[original.pk]
        if fill is not None:
            move_specific_plant(line.allocation.plant, {
                'location_type': SpecificPlantLocation.CONTAINER_UNIT, 'container_unit': fill.inventory_unit,
                'started': sales_return.returned_at, 'notes': sales_return.reason,
            }, user)
        return None, None, fill
    destination = destination or original.source
    reason, when = sales_return.reason, sales_return.returned_at
    reference = f'return:{sales_return.pk}:pot:{dispatch.pk}'
    if original.unit_id:
        movement = post_unit_movement(order.workspace, user, UnitMovementRequest(
            original.unit, StockMovement.MovementType.CUSTOMER_RETURN, destination,
            occurred_at=when, reason=reason, reference=reference,
        ))
    else:
        movement = post_stock_movement(order.workspace, user, MovementRequest(
            original.lot, StockMovement.MovementType.CUSTOMER_RETURN, Decimal('1'), destination=destination,
            occurred_at=when, reason=reason, reference=reference,
        ))
    discard = None
    fill = None
    if outcome == SalesReturnLine.Outcome.DISCARDED:
        if original.unit_id:
            discard = post_unit_movement(order.workspace, user, UnitMovementRequest(
                original.unit, StockMovement.MovementType.WASTE, occurred_at=when, reason=reason, reference=reference,
            ))
        else:
            discard = post_stock_movement(order.workspace, user, MovementRequest(
                original.lot, StockMovement.MovementType.WASTE, Decimal('1'), source=destination,
                occurred_at=when, reason=reason, reference=reference,
            ))
    else:
        if original.unit_id:
            fill = open_numbered_fill(order.workspace, user, original.unit, opened_at=when, notes=reason, returned=True)
            target = {'location_type': SpecificPlantLocation.CONTAINER_UNIT, 'container_unit': fill.inventory_unit}
        else:
            fill = open_counted_fill(order.workspace, user, original.lot, destination, 1, opened_at=when, notes=reason, returned=True)
            target = {'location_type': SpecificPlantLocation.LOCATION, 'location': destination, 'container_fill': fill}
        move_specific_plant(line.allocation.plant, {**target, 'started': when, 'notes': reason}, user)
    recovered[original.pk] = (movement, discard, fill)
    return movement, discard, fill


def withdraw_returned_pot(line, user, when, reason):
    """Refuse to undo a return after the plant or its new fill has moved on."""
    fill = line.container_fill
    if fill is None:
        return
    fill, = lock_pot_fills(line.sales_return.workspace, [fill.pk])
    placements = list(fill.plant_locations.all())
    expected = set(line.sales_return.lines.filter(container_fill=fill).values_list('fulfillment_line__allocation__plant_id', flat=True))
    if {row.specific_plant_id for row in placements} != expected or any(row.ended is not None or row.numbered_at is not None for row in placements) or fill.status != 'open':
        raise ValidationError({'sales_return': 'The returned pot or plant has moved on.'})
    if fill.inventory_unit_id:
        movement = line.sales_return.lines.filter(container_fill=fill, return_movement__isnull=False).get().return_movement
        if StockMovement.objects.filter(unit_id=fill.inventory_unit_id, pk__gt=movement.pk).exists():
            raise ValidationError({'sales_return': 'The returned numbered pot has moved on.'})
    if fill.container_application_targets.exists():
        raise ValidationError({'sales_return': 'The returned fill has received new media.'})
    for placement in placements:
        placement.ended = when
        placement.save(update_fields=['ended'])
    close_dispatched_fill(fill, user, when, reason)
