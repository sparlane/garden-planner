"""Bidirectional traceability between sold output and exact input lots."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from rest_framework.exceptions import NotFound

from costing.models import CostAllocation
from costing.services import plant_cost_breakdown
from inventory.models import StockLot, StockMovement
from plantings.lifecycle import lifecycle_summaries
from plantings.models import SpecificPlant
from sales.models import FulfillmentLine

from .common import Report, decimal_string


def _source_lot(layer):
    if layer.application_line_id:
        return layer.application_line.lot
    if layer.sowing_posting_id:
        return layer.sowing_posting.movement.lot
    if layer.generation_residual_id:
        return layer.generation_residual.lot
    return None


def _effective_layers(queryset):
    return queryset.filter(
        reversal_of__isnull=True, reversal__isnull=True,
    ).select_related(
        'batch__variety',
        'application_line__application', 'application_line__lot__item',
        'application_line__lot__receipt_line__receipt__supplier',
        'sowing_posting__movement__lot__item',
        'sowing_posting__movement__lot__receipt_line__receipt__supplier',
        'generation_residual__lot__item',
        'generation_residual__lot__receipt_line__receipt__supplier',
        'garden_planting__supplier',
        'specific_plant', 'plant_cohort', 'seed_tray_cell',
    ).order_by('pk')


def _lot_reference(lot):
    receipt = None
    supplier = None
    if lot and lot.receipt_line_id:
        receipt = lot.receipt_line.receipt
        supplier = receipt.supplier
    return {
        'lot_id': getattr(lot, 'pk', None),
        'lot_identifier': getattr(lot, 'identifier', None),
        'item_id': getattr(lot, 'item_id', None),
        'item_name': lot.item.name if lot else None,
        'receipt_line_id': getattr(lot, 'receipt_line_id', None),
        'receipt_id': getattr(receipt, 'pk', None),
        'supplier_id': getattr(supplier, 'pk', None),
        'supplier_name': getattr(supplier, 'name', None),
    }


def plant_trace(workspace, plant_id, filters):  # pylint: disable=too-many-locals
    """Trace one identified plant from inputs through its commercial history."""
    plant = SpecificPlant.objects.filter(
        workspace=workspace, pk=plant_id,
    ).select_related(
        'batch__variety',
        'cell_planting__cell',
        'cell_planting__seed_tray_planting__generation',
        'cell_planting__seed_tray_planting__seeds_used__seeds__supplier',
        'promoted_from_cohort',
        'garden_planting__supplier',
    ).first()
    if plant is None:
        raise NotFound('Plant not found.')
    fulfillment_lines = list(FulfillmentLine.objects.filter(
        allocation__plant=plant,
        fulfillment__reversal_of__isnull=True,
        fulfillment__reversal__isnull=True,
    ).select_related(
        'fulfillment__order__customer', 'allocation__line',
    ).order_by('fulfillment__fulfilled_at', 'pk'))
    commerce = [{
        'fulfillment_line_id': line.pk,
        'fulfillment_id': line.fulfillment_id,
        'fulfillment_number': line.fulfillment.fulfillment_number,
        'fulfilled_at': line.fulfillment.fulfilled_at,
        'order_id': line.fulfillment.order_id,
        'order_number': line.fulfillment.order.order_number,
        'customer_id': line.fulfillment.order.customer_id,
        'customer_name': (
            line.fulfillment.order.customer.name
            if line.fulfillment.order.customer_id else None
        ),
        'revenue_ex_tax': decimal_string(line.subtotal_ex_tax, 4),
        'cogs_amount': decimal_string(line.cogs_amount, 4),
        'cogs_provisional': line.cogs_provisional,
    } for line in fulfillment_lines]
    origin = plant.cell_planting
    garden_origin = plant.garden_planting
    sowing = origin.seed_tray_planting if origin else None
    seed_packet = sowing.seeds_used if sowing else None
    layers = list(_effective_layers(CostAllocation.objects.filter(specific_plant=plant)))
    rows = []
    for layer in layers or [None]:
        lot = _source_lot(layer) if layer else None
        rows.append({
            'plant_id': plant.pk,
            'batch_id': plant.batch_id,
            'batch_code': plant.batch.code,
            'variety_id': plant.batch.variety_id,
            'variety_name': plant.batch.variety.name,
            'cohort_id': plant.promoted_from_cohort_id,
            'cell_id': getattr(origin, 'cell_id', None),
            'generation_id': getattr(sowing, 'generation_id', None),
            'seed_packet_id': getattr(seed_packet, 'pk', None),
            'seed_supplier_id': (
                seed_packet.seeds.supplier_id if seed_packet else getattr(garden_origin, 'supplier_id', None)
            ),
            'allocation_id': getattr(layer, 'pk', None),
            'source_type': getattr(layer, 'source_type', None),
            'source_id': getattr(layer, 'source_id', None),
            'application_id': (
                layer.application_line.application_id
                if layer and layer.application_line_id else None
            ),
            'movement_id': getattr(layer, 'movement_id', None),
            **_lot_reference(lot),
            'quantity': decimal_string(getattr(layer, 'base_quantity', None), 9),
            'base_unit': getattr(layer, 'base_unit', None),
            'cost_amount': decimal_string(getattr(layer, 'amount', None), 4),
            'currency_code': getattr(layer, 'currency_code', workspace.currency_code),
            'unvalued': bool(layer and layer.amount is None),
            'commerce': commerce,
        })
    cost = plant_cost_breakdown(plant)
    summary = lifecycle_summaries([plant.pk])[plant.pk]
    return Report(
        name=f'plant-trace-{plant.pk}', filters=filters, rows=rows,
        columns=tuple(rows[0]),
        totals={
            'plant_id': plant.pk,
            'lifecycle_state': summary.state,
            'fulfillments': len(commerce),
            'provisional_value': cost['provisional_value'],
            'final_value': cost['final_value'],
            'currency_code': cost['currency_code'],
        },
        reconciliation={
            'cost_layers': len(layers),
            'cost_breakdown_url': f'/costing/plants/{plant.pk}/',
        },
        data_quality=([{
            'code': 'unvalued_input',
            'count': sum(layer.amount is None for layer in layers),
            'message': 'At least one exact input cost is unknown.',
            'drill_down': f'/reports/traceability/plants/{plant.pk}/',
        }] if cost['unknown_cost'] else []),
    )


def _lot_balances(lot):
    balances = defaultdict(Decimal)
    names = {}
    for movement in StockMovement.objects.filter(lot=lot).select_related(
            'source', 'destination'):
        if movement.source_id:
            balances[movement.source_id] -= movement.quantity
            names[movement.source_id] = movement.source.name
        if movement.destination_id:
            balances[movement.destination_id] += movement.quantity
            names[movement.destination_id] = movement.destination.name
    return [{
        'location_id': location_id,
        'location_name': names[location_id],
        'quantity': decimal_string(quantity, 9),
        'base_unit': lot.item.base_unit,
    } for location_id, quantity in sorted(balances.items()) if quantity != 0]


def lot_trace(workspace, lot_id, filters):  # pylint: disable=too-many-locals
    """Trace one exact input lot forward to output, commerce, loss, and balance."""
    lot = StockLot.objects.filter(
        workspace=workspace, pk=lot_id,
    ).select_related('item', 'receipt_line__receipt__supplier').first()
    if lot is None:
        raise NotFound('Lot not found.')
    lot_sources = Q(application_line__lot=lot)
    lot_sources |= Q(sowing_posting__movement__lot=lot)
    lot_sources |= Q(generation_residual__lot=lot)
    layers = list(_effective_layers(CostAllocation.objects.filter(lot_sources)))
    plant_ids = [layer.specific_plant_id for layer in layers if layer.specific_plant_id]
    summaries = lifecycle_summaries(plant_ids)
    fulfillment_by_plant = defaultdict(list)
    for line in FulfillmentLine.objects.filter(
            allocation__plant_id__in=plant_ids,
            fulfillment__reversal_of__isnull=True,
            fulfillment__reversal__isnull=True).select_related(
                'allocation', 'fulfillment__order__customer'):
        fulfillment_by_plant[line.allocation.plant_id].append({
            'fulfillment_line_id': line.pk,
            'fulfillment_id': line.fulfillment_id,
            'fulfillment_number': line.fulfillment.fulfillment_number,
            'order_id': line.fulfillment.order_id,
            'order_number': line.fulfillment.order.order_number,
            'customer_id': line.fulfillment.order.customer_id,
        })
    balances = _lot_balances(lot)
    rows = [{
        **_lot_reference(lot),
        'allocation_id': layer.pk,
        'batch_id': layer.batch_id,
        'batch_code': layer.batch.code,
        'variety_id': layer.batch.variety_id,
        'variety_name': layer.batch.variety.name,
        'target_type': layer.target_type,
        'cell_id': layer.seed_tray_cell_id,
        'cohort_id': layer.plant_cohort_id,
        'plant_id': layer.specific_plant_id,
        'plant_state': (
            summaries[layer.specific_plant_id].state
            if layer.specific_plant_id else None
        ),
        'production_loss': layer.target_type == CostAllocation.TargetType.PRODUCTION_LOSS,
        'quantity': decimal_string(layer.base_quantity, 9),
        'base_unit': layer.base_unit,
        'cost_amount': decimal_string(layer.amount, 4),
        'currency_code': layer.currency_code,
        'unvalued': layer.amount is None,
        'fulfillments': fulfillment_by_plant[layer.specific_plant_id],
        'remaining_balances': balances,
    } for layer in layers]
    if not rows:
        rows.append({
            **_lot_reference(lot),
            'allocation_id': None, 'batch_id': None, 'batch_code': None,
            'variety_id': None, 'variety_name': None, 'target_type': None,
            'cell_id': None, 'cohort_id': None, 'plant_id': None,
            'plant_state': None, 'production_loss': False, 'quantity': None,
            'base_unit': lot.item.base_unit, 'cost_amount': None,
            'currency_code': lot.currency_code,
            'unvalued': lot.base_unit_cost is None, 'fulfillments': [],
            'remaining_balances': balances,
        })
    unvalued = sum(row['unvalued'] for row in rows)
    return Report(
        name=f'lot-trace-{lot.pk}', filters=filters, rows=rows,
        columns=tuple(rows[0]),
        totals={
            'lot_id': lot.pk,
            'allocations': len(layers),
            'affected_batches': len({layer.batch_id for layer in layers}),
            'affected_plants': len(set(plant_ids)),
            'remaining_balances': balances,
        },
        reconciliation={
            'movement_history_url': f'/reports/inventory-movements/?lot={lot.pk}',
            'allocation_quantity': decimal_string(
                sum((layer.base_quantity for layer in layers), Decimal('0')), 9,
            ),
        },
        data_quality=([{
            'code': 'unvalued_input', 'count': unvalued,
            'message': 'This lot or one of its allocations has unknown cost.',
            'drill_down': f'/reports/traceability/lots/{lot.pk}/',
        }] if unvalued else []),
    )
