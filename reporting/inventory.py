"""Inventory report calculations derived from immutable stock facts."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from inventory.models import (
    InventoryItem,
    InventoryUnit,
    StockLot,
    StockMovement,
    StocktakeLine,
    StocktakeVariance,
)
from locations.models import Location
from sales.models import SalesOrderAllocation
from seedtrays.models import SeedTrayGeneration

from .common import Report, decimal_string


QUANTITY_ZERO = Decimal('0')
MONEY_ZERO = Decimal('0')


def _quality(code, count, message, url):
    return {'code': code, 'count': count, 'message': message, 'drill_down': url}


def inventory_balances(workspace, filters):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Derive on-hand, reserved, and available stock by exact lot and place."""
    lots = StockLot.objects.filter(workspace=workspace).select_related('item')
    if filters.get('item'):
        lots = lots.filter(item_id=filters['item'])
    if filters.get('lot'):
        lots = lots.filter(pk=filters['lot'])
    if filters.get('expires_before'):
        lots = lots.filter(expires_on__lte=filters['expires_before'])
    lots = list(lots.order_by('item__name', 'identifier', 'pk'))
    lot_ids = [lot.pk for lot in lots]
    lots_by_id = {lot.pk: lot for lot in lots}

    balances = defaultdict(Decimal)
    location_ids = set()
    movements = StockMovement.objects.filter(
        workspace=workspace, lot_id__in=lot_ids,
    ).values('lot_id', 'source_id', 'destination_id', 'quantity')
    for movement in movements:
        if movement['source_id']:
            balances[(movement['lot_id'], movement['source_id'])] -= movement['quantity']
            location_ids.add(movement['source_id'])
        if movement['destination_id']:
            balances[(movement['lot_id'], movement['destination_id'])] += movement['quantity']
            location_ids.add(movement['destination_id'])

    reserved = defaultdict(int)
    reservations = SalesOrderAllocation.objects.filter(
        status=SalesOrderAllocation.Status.RESERVED,
        inventory_unit__workspace=workspace,
        inventory_unit__source_lot_id__in=lot_ids,
        inventory_unit__current_location__isnull=False,
    ).values_list(
        'inventory_unit__source_lot_id', 'inventory_unit__current_location_id',
    )
    for lot_id, location_id in reservations:
        reserved[(lot_id, location_id)] += 1
    locations = {
        row.pk: row for row in Location.objects.filter(pk__in=location_ids)
    }

    item_available = defaultdict(Decimal)
    for (lot_id, _location_id), physical in balances.items():
        lot = lots_by_id[lot_id]
        item_available[lot.item_id] += physical - reserved[(lot_id, _location_id)]

    rows = []
    for lot in lots:
        for (lot_id, location_id), physical in balances.items():
            if lot_id != lot.pk or physical == 0:
                continue
            if filters.get('location') and location_id != filters['location']:
                continue
            reserved_quantity = Decimal(reserved[(lot_id, location_id)])
            available = physical - reserved_quantity
            low_stock = lot.item.reorder_level is not None and (
                item_available[lot.item_id] <= lot.item.reorder_level
            )
            if 'low_stock' in filters and filters['low_stock'] != low_stock:
                continue
            physical_value = None
            reserved_value = None
            available_value = None
            if lot.base_unit_cost is not None:
                physical_value = physical * lot.base_unit_cost
                reserved_value = reserved_quantity * lot.base_unit_cost
                available_value = available * lot.base_unit_cost
            rows.append({
                'item_id': lot.item_id,
                'item_name': lot.item.name,
                'lot_id': lot.pk,
                'lot_identifier': lot.identifier,
                'location_id': location_id,
                'location_name': locations[location_id].name,
                'expires_on': lot.expires_on,
                'physical_quantity': decimal_string(physical, 9),
                'reserved_quantity': decimal_string(reserved_quantity, 9),
                'available_quantity': decimal_string(available, 9),
                'base_unit': lot.item.base_unit,
                'unit_cost': decimal_string(lot.base_unit_cost, 12),
                'physical_value': decimal_string(physical_value, 4),
                'reserved_value': decimal_string(reserved_value, 4),
                'available_value': decimal_string(available_value, 4),
                'currency_code': lot.currency_code,
                'low_stock': low_stock,
                'quantity_certainty': lot.quantity_certainty,
                'unvalued': lot.base_unit_cost is None,
            })

    quantity_totals = defaultdict(lambda: [Decimal('0'), Decimal('0'), Decimal('0')])
    value_totals = defaultdict(lambda: [Decimal('0'), Decimal('0'), Decimal('0')])
    for row in rows:
        quantities = quantity_totals[row['base_unit']]
        quantities[0] += Decimal(row['physical_quantity'])
        quantities[1] += Decimal(row['reserved_quantity'])
        quantities[2] += Decimal(row['available_quantity'])
        if not row['unvalued']:
            values = value_totals[row['currency_code']]
            values[0] += Decimal(row['physical_value'])
            values[1] += Decimal(row['reserved_value'])
            values[2] += Decimal(row['available_value'])
    unvalued = sum(row['unvalued'] for row in rows)
    uncertain = sum(row['quantity_certainty'] != 'exact' for row in rows)
    quality = []
    if unvalued:
        quality.append(_quality(
            'unvalued_inventory', unvalued,
            'One or more on-hand lots have no acquisition cost.',
            '/reports/inventory-balances/?unvalued=true',
        ))
    if uncertain:
        quality.append(_quality(
            'uncertain_quantity', uncertain,
            'One or more balances originate from estimated or unknown stock.',
            '/reports/inventory-balances/?uncertain=true',
        ))
    totals = {
        'quantities': [{
            'base_unit': unit,
            'physical': decimal_string(values[0], 9),
            'reserved': decimal_string(values[1], 9),
            'available': decimal_string(values[2], 9),
        } for unit, values in sorted(quantity_totals.items())],
        'valuations': [{
            'currency_code': currency,
            'physical': decimal_string(values[0], 4),
            'reserved': decimal_string(values[1], 4),
            'available': decimal_string(values[2], 4),
        } for currency, values in sorted(value_totals.items())],
        'unvalued_rows': unvalued,
    }
    return Report(
        name='inventory-balances', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'item_id', 'item_name', 'lot_id', 'lot_identifier', 'location_id',
            'location_name', 'expires_on', 'physical_quantity',
            'reserved_quantity', 'available_quantity', 'base_unit', 'unit_cost',
            'physical_value', 'reserved_value', 'available_value',
            'currency_code', 'low_stock', 'quantity_certainty', 'unvalued',
        ),
        totals=totals,
        reconciliation={'quantity_equation': 'physical = reserved + available'},
        data_quality=quality,
    )


def serialized_trays(workspace, filters):  # pylint: disable=too-many-locals
    """Report exact tray identities, physical state, use, and reservation."""
    units = InventoryUnit.objects.filter(
        workspace=workspace, item__category=InventoryItem.Category.TRAY,
    ).select_related('item', 'source_lot', 'current_location', 'seed_tray')
    if filters.get('item'):
        units = units.filter(item_id=filters['item'])
    if filters.get('location'):
        units = units.filter(current_location_id=filters['location'])
    units = list(units.order_by('asset_code', 'pk'))
    unit_ids = [unit.pk for unit in units]
    latest = {}
    for movement in StockMovement.objects.filter(
            unit_id__in=unit_ids, reversal_of__isnull=True,
            reversal__isnull=True).order_by('unit_id', 'occurred_at', 'pk'):
        latest[movement.unit_id] = movement
    reserved = set(SalesOrderAllocation.objects.filter(
        status=SalesOrderAllocation.Status.RESERVED,
        inventory_unit_id__in=unit_ids,
    ).values_list('inventory_unit_id', flat=True))
    active_generations = {
        row.tray_id: row for row in SeedTrayGeneration.objects.filter(
            tray__inventory_unit_id__in=unit_ids,
            status=SeedTrayGeneration.Status.OPEN,
        )
    }
    occupied_trays = set()
    from plantings.models import SeedTrayPlanting, SpecificPlantLocation  # pylint: disable=import-outside-toplevel
    occupied_trays.update(SeedTrayPlanting.objects.filter(
        seed_tray__inventory_unit_id__in=unit_ids, removed=False,
    ).values_list('seed_tray_id', flat=True))
    occupied_trays.update(SpecificPlantLocation.objects.filter(
        seed_tray_cell__tray__inventory_unit_id__in=unit_ids, ended__isnull=True,
    ).values_list('seed_tray_cell__tray_id', flat=True))

    rows = []
    for unit in units:
        movement = latest.get(unit.pk)
        if unit.current_location_id:
            if unit.current_location.location_type == Location.LocationType.QUARANTINE:
                state = 'quarantined'
            elif movement and movement.movement_type in {
                    StockMovement.MovementType.ADJUSTMENT_GAIN,
                    StockMovement.MovementType.CUSTOMER_RETURN}:
                state = 'returned'
            else:
                state = 'available'
        else:
            state = {
                StockMovement.MovementType.ADJUSTMENT_LOSS: 'lost',
                StockMovement.MovementType.WASTE: 'retired',
                StockMovement.MovementType.SALE: 'dispatched',
            }.get(getattr(movement, 'movement_type', None), 'retired')
        tray = unit.seed_tray
        in_use = tray.pk in occupied_trays
        if filters.get('physical_state') and state != filters['physical_state']:
            continue
        if 'in_use' in filters and in_use != filters['in_use']:
            continue
        generation = active_generations.get(tray.pk)
        rows.append({
            'unit_id': unit.pk,
            'asset_code': unit.asset_code,
            'item_id': unit.item_id,
            'item_name': unit.item.name,
            'source_lot_id': unit.source_lot_id,
            'tray_id': tray.pk,
            'location_id': unit.current_location_id,
            'location_name': (
                unit.current_location.name if unit.current_location_id else None
            ),
            'physical_state': state,
            'in_use': in_use,
            'reserved': unit.pk in reserved,
            'available': state in {'available', 'returned'} and unit.pk not in reserved,
            'generation_id': getattr(generation, 'pk', None),
            'generation_code': getattr(generation, 'code', None),
            'acquisition_cost': decimal_string(unit.acquisition_cost, 4),
            'currency_code': unit.currency_code,
            'unvalued': unit.acquisition_cost is None,
        })
    unvalued = sum(row['unvalued'] for row in rows)
    quality = []
    if unvalued:
        quality.append(_quality(
            'unvalued_serialized_stock', unvalued,
            'One or more trays have no reconciled acquisition cost.',
            '/reports/serialized-trays/?unvalued=true',
        ))
    return Report(
        name='serialized-trays', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'unit_id', 'asset_code', 'item_id', 'item_name', 'source_lot_id',
            'tray_id', 'location_id', 'location_name', 'physical_state',
            'in_use', 'reserved', 'available', 'generation_id',
            'generation_code', 'acquisition_cost', 'currency_code', 'unvalued',
        ),
        totals={
            'units': len(rows),
            'available': sum(row['available'] for row in rows),
            'reserved': sum(row['reserved'] for row in rows),
            'in_use': sum(row['in_use'] for row in rows),
            'unvalued': unvalued,
        },
        reconciliation={'identity_equation': 'units = one row per serialized tray'},
        data_quality=quality,
    )


def movement_history(workspace, filters):
    """Return immutable stock movement history with exact provenance."""
    queryset = StockMovement.objects.filter(workspace=workspace).select_related(
        'lot__item', 'unit', 'source', 'destination',
    )
    if filters.get('date_from'):
        queryset = queryset.filter(occurred_at__gte=filters['date_from'])
    if filters.get('date_to'):
        queryset = queryset.filter(occurred_at__lte=filters['date_to'])
    if filters.get('item'):
        queryset = queryset.filter(lot__item_id=filters['item'])
    if filters.get('lot'):
        queryset = queryset.filter(lot_id=filters['lot'])
    if filters.get('location'):
        queryset = queryset.filter(
            Q(source_id=filters['location']) | Q(destination_id=filters['location']),
        )
    if filters.get('movement_type'):
        valid = {choice for choice, _label in StockMovement.MovementType.choices}
        if filters['movement_type'] not in valid:
            from rest_framework.exceptions import ValidationError  # pylint: disable=import-outside-toplevel
            raise ValidationError({'movement_type': 'Select a valid movement type.'})
        queryset = queryset.filter(movement_type=filters['movement_type'])
    if filters.get('reference'):
        queryset = queryset.filter(reference__icontains=filters['reference'])
    rows = [{
        'movement_id': row.pk,
        'occurred_at': row.occurred_at,
        'movement_type': row.movement_type,
        'item_id': row.lot.item_id,
        'item_name': row.lot.item.name,
        'lot_id': row.lot_id,
        'lot_identifier': row.lot.identifier,
        'unit_id': row.unit_id,
        'asset_code': row.unit.asset_code if row.unit_id else None,
        'quantity': decimal_string(row.quantity, 9),
        'base_unit': row.lot.item.base_unit,
        'source_id': row.source_id,
        'source_name': row.source.name if row.source_id else None,
        'destination_id': row.destination_id,
        'destination_name': row.destination.name if row.destination_id else None,
        'reference': row.reference,
        'reason': row.reason,
        'reversal_of_id': row.reversal_of_id,
        'receipt_line_id': row.receipt_line_id,
        'stocktake_line_id': row.stocktake_line_id,
    } for row in queryset.order_by('-occurred_at', '-pk')]
    return Report(
        name='inventory-movements', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'movement_id', 'occurred_at', 'movement_type', 'item_id',
            'item_name', 'lot_id', 'lot_identifier', 'unit_id', 'asset_code',
            'quantity', 'base_unit', 'source_id', 'source_name',
            'destination_id', 'destination_name', 'reference', 'reason',
            'reversal_of_id', 'receipt_line_id', 'stocktake_line_id',
        ),
        totals={'movements': len(rows)},
        reconciliation={'history': 'reversals remain separate immutable rows'},
    )


def stocktake_variances(workspace, filters):
    """Unify current generic variances and legacy exact-lot count differences."""
    current = StocktakeVariance.objects.filter(
        target__stocktake__workspace=workspace,
    ).select_related(
        'target__stocktake', 'target__expected_location',
        'target__accepted_count__observed_location',
    )
    legacy = StocktakeLine.objects.filter(
        stocktake__workspace=workspace,
        variance_base_quantity__isnull=False,
    ).exclude(variance_base_quantity=0).select_related(
        'stocktake', 'lot__item', 'location',
    )
    if filters.get('stocktake'):
        current = current.filter(target__stocktake_id=filters['stocktake'])
        legacy = legacy.filter(stocktake_id=filters['stocktake'])
    if filters.get('date_from'):
        current = current.filter(target__stocktake__counted_at__gte=filters['date_from'])
        legacy = legacy.filter(stocktake__counted_at__gte=filters['date_from'])
    if filters.get('date_to'):
        current = current.filter(target__stocktake__counted_at__lte=filters['date_to'])
        legacy = legacy.filter(stocktake__counted_at__lte=filters['date_to'])
    if filters.get('location'):
        current = current.filter(Q(
            target__expected_location_id=filters['location'],
        ) | Q(
            target__accepted_count__observed_location_id=filters['location'],
        ))
        legacy = legacy.filter(location_id=filters['location'])
    if filters.get('kind'):
        current = current.filter(kind=filters['kind'])
        if filters['kind'] != 'quantity':
            legacy = legacy.none()
    rows = [{
        'source_kind': 'target',
        'variance_id': row.pk,
        'stocktake_id': row.target.stocktake_id,
        'counted_at': row.target.stocktake.counted_at,
        'status': row.target.stocktake.status,
        'target_type': row.target.target_type,
        'target_id': row.target.target_object_id,
        'display': row.target.display,
        'kind': row.kind,
        'expected': row.expected,
        'observed': row.observed,
        'expected_location_id': row.target.expected_location_id,
        'observed_location_id': (
            row.target.accepted_count.observed_location_id
            if row.target.accepted_count_id else None
        ),
        'source_changed': row.source_changed,
        'resolution_action': row.resolution_action,
        'resolved_at': row.resolved_at,
    } for row in current.order_by('-target__stocktake__counted_at', 'pk')]
    rows.extend({
        'source_kind': 'lot_line',
        'variance_id': row.pk,
        'stocktake_id': row.stocktake_id,
        'counted_at': row.stocktake.counted_at,
        'status': row.stocktake.status,
        'target_type': 'lot',
        'target_id': row.lot_id,
        'display': f'{row.lot.item.name}: {row.lot.identifier}',
        'kind': 'quantity',
        'expected': {'quantity': decimal_string(row.expected_base_quantity, 9)},
        'observed': {'quantity': decimal_string(row.counted_base_quantity, 9)},
        'expected_location_id': row.location_id,
        'observed_location_id': row.location_id,
        'source_changed': False,
        'resolution_action': 'posted' if row.stocktake.posted_at else '',
        'resolved_at': row.stocktake.posted_at,
    } for row in legacy.order_by('-stocktake__counted_at', 'pk'))
    rows.sort(key=lambda row: (row['counted_at'], row['variance_id']), reverse=True)
    unresolved = sum(not row['resolved_at'] for row in rows)
    quality = []
    if unresolved:
        quality.append(_quality(
            'unresolved_stocktake_variance', unresolved,
            'One or more stocktake differences have no posted resolution.',
            '/reports/stocktake-variances/?resolved=false',
        ))
    return Report(
        name='stocktake-variances', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'source_kind', 'variance_id', 'stocktake_id', 'counted_at',
            'status', 'target_type', 'target_id', 'display', 'kind', 'expected',
            'observed', 'expected_location_id', 'observed_location_id',
            'source_changed', 'resolution_action', 'resolved_at',
        ),
        totals={'variances': len(rows), 'unresolved': unresolved},
        reconciliation={'sources': 'target variances + legacy lot lines'},
        data_quality=quality,
    )
