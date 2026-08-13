"""Frozen-scope counting and review services for nursery stocktakes."""

# Mixed stocktakes intentionally coordinate several authoritative domains.
# pylint: disable=too-many-locals,too-many-branches,too-many-arguments,too-many-positional-arguments

from decimal import Decimal
from hashlib import sha256
import json

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from locations.models import Location
from plantings.growth import current_growth
from plantings.lifecycle import plant_lifecycle_summary
from plantings.models import PlantCohort, SpecificPlant, SpecificPlantLocation
from seeds.models import SeedPacket
from seeds.services import packet_inventory_snapshot
from seedtrays.models import SeedTray

from .ledger import physical_balance, quantize_quantity, unit_physical_state
from .models import (
    InventoryItem,
    StockLot,
    Stocktake,
    StocktakeCount,
    StocktakeTarget,
    StocktakeVariance,
)


QUANTITY_TARGETS = {
    StocktakeTarget.TargetType.LOT,
    StocktakeTarget.TargetType.SEED_PACKET,
    StocktakeTarget.TargetType.COHORT,
}
IDENTITY_TARGETS = {
    StocktakeTarget.TargetType.TRAY,
    StocktakeTarget.TargetType.PLANT,
}


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return sha256(encoded).hexdigest()


def _location_ids(workspace, scope):
    location_id = scope.get('location')
    if not location_id:
        raise ValidationError({'location': 'Select a stocktake location.'})
    try:
        location = Location.objects.get(pk=location_id, workspace=workspace, active=True)
    except Location.DoesNotExist as exc:
        raise ValidationError({'location': 'Select an active location.'}) from exc
    queryset = location.subtree() if scope.get('include_descendants') else Location.objects.filter(pk=location.pk)
    return location, set(queryset.values_list('pk', flat=True))


def _active_code(target):
    """Return the current scannable code without requiring every target to have one."""
    from labels.models import LabelCode, LabelIdentity  # pylint: disable=import-outside-toplevel

    content_type = ContentType.objects.get_for_model(target, for_concrete_model=True)
    identity = LabelIdentity.objects.filter(
        workspace=target.workspace,
        target_content_type=content_type,
        target_object_id=target.pk,
    ).first()
    code = LabelCode.objects.filter(identity=identity, status=LabelCode.Status.ACTIVE).first() if identity else None
    return code.code if code else ''


def _target(target_type, key, instance, display, location, quantity, state, snapshot):
    return {
        'target_type': target_type,
        'target_key': key,
        'target_object_id': instance.pk,
        'display': display,
        'expected_location': location,
        'expected_quantity': quantity,
        'expected_state': state or '',
        'expected_snapshot': snapshot,
        'source_revision': _digest(snapshot),
    }


def _lot_targets(workspace, location_ids, scope):
    packets = set(SeedPacket.objects.filter(workspace=workspace, stock_lot__isnull=False).values_list('stock_lot_id', flat=True))
    lots = StockLot.objects.filter(
        workspace=workspace,
        item__active=True,
        item__tracking_mode=InventoryItem.TrackingMode.LOT,
    ).exclude(pk__in=packets).select_related('item')
    if scope.get('item'):
        lots = lots.filter(item_id=scope['item'])
    if scope.get('category'):
        lots = lots.filter(item__category=scope['category'])
    locations = Location.objects.filter(pk__in=location_ids)
    rows = []
    for lot in lots:
        for location in locations:
            quantity = quantize_quantity(physical_balance(lot, location))
            if quantity <= 0:
                continue
            snapshot = {
                'quantity': str(quantity), 'location': location.pk,
                'item': lot.item_id, 'unit': lot.item.base_unit,
                'last_movement': lot.movements.order_by('-pk').values_list('pk', flat=True).first(),
                'unit_cost': str(lot.base_unit_cost) if lot.base_unit_cost is not None else None,
                'currency': lot.currency_code,
            }
            rows.append(_target(
                StocktakeTarget.TargetType.LOT, f'lot:{lot.pk}:location:{location.pk}',
                lot, f'{lot.item.name} · {lot.identifier}', location,
                quantity, '', snapshot,
            ))
    return rows


def _packet_targets(workspace, location_ids, scope):
    packets = SeedPacket.objects.filter(
        workspace=workspace, storage_location_id__in=location_ids,
        stock_lot__isnull=False,
    ).select_related('seeds', 'stock_lot__item', 'storage_location')
    if scope.get('item'):
        packets = packets.filter(stock_lot__item_id=scope['item'])
    if scope.get('category') and scope['category'] != InventoryItem.Category.SEED:
        return []
    rows = []
    for packet in packets:
        inventory = packet_inventory_snapshot(packet)
        quantity = inventory['remaining_quantity']
        snapshot = {
            'quantity': str(quantity) if quantity is not None else None,
            'certainty': inventory['quantity_certainty'],
            'location': packet.storage_location_id,
            'lot': packet.stock_lot_id,
            'last_reconciliation': packet.quantity_reconciliations.order_by('-pk').values_list('pk', flat=True).first(),
            'last_movement': packet.stock_lot.movements.order_by('-pk').values_list('pk', flat=True).first(),
            'unit': inventory['base_unit'],
            'unit_cost': str(inventory['effective_base_unit_cost']) if inventory['effective_base_unit_cost'] is not None else None,
            'currency': inventory['currency_code'],
        }
        rows.append(_target(
            StocktakeTarget.TargetType.SEED_PACKET, f'seed_packet:{packet.pk}',
            packet, str(packet.seeds), packet.storage_location,
            quantity, inventory['quantity_certainty'], snapshot,
        ))
    return rows


def _tray_targets(workspace, location_ids, scope):
    trays = SeedTray.objects.filter(
        workspace=workspace,
        inventory_unit__current_location_id__in=location_ids,
    ).select_related('inventory_unit__current_location', 'inventory_unit__item')
    if scope.get('item'):
        trays = trays.filter(inventory_unit__item_id=scope['item'])
    if scope.get('category') and scope['category'] != InventoryItem.Category.TRAY:
        return []
    rows = []
    for tray in trays:
        unit = tray.inventory_unit
        state = unit_physical_state(unit)
        if scope.get('tray_state') and scope['tray_state'] != state:
            continue
        snapshot = {
            'location': unit.current_location_id, 'state': state,
            'unit': unit.pk, 'asset_code': unit.asset_code,
            'label_code': _active_code(tray),
            'last_movement': unit.movements.order_by('-pk').values_list('pk', flat=True).first(),
            'acquisition_cost': str(unit.acquisition_cost) if unit.acquisition_cost is not None else None,
            'currency': unit.currency_code,
        }
        rows.append(_target(
            StocktakeTarget.TargetType.TRAY, f'tray:{tray.pk}', tray,
            str(tray), unit.current_location, Decimal('1'), state, snapshot,
        ))
    return rows


def _cohort_targets(workspace, location_ids, scope):
    cohorts = PlantCohort.objects.filter(
        workspace=workspace, location_id__in=location_ids, quantity__gt=0,
    ).select_related('location', 'batch__variety__plant')
    if scope.get('variety'):
        cohorts = cohorts.filter(batch__variety_id=scope['variety'])
    rows = []
    for cohort in cohorts:
        growth = current_growth(cohort)
        stage = growth['stage']
        if scope.get('stage') and (stage is None or stage.pk != int(scope['stage'])):
            continue
        snapshot = {
            'quantity': cohort.quantity, 'location': cohort.location_id,
            'state': cohort.lifecycle_state, 'revision': cohort.revision,
            'variety': cohort.batch.variety_id,
            'stage': stage.pk if stage else None, 'label_code': _active_code(cohort),
        }
        rows.append(_target(
            StocktakeTarget.TargetType.COHORT, f'cohort:{cohort.pk}', cohort,
            str(cohort), cohort.location, Decimal(cohort.quantity),
            cohort.lifecycle_state, snapshot,
        ))
    return rows


def _plant_location(plant):
    placement = plant.locations.filter(ended__isnull=True).select_related(
        'location', 'seed_tray_cell__seed_tray__inventory_unit__current_location',
    ).first()
    if placement is None:
        return None
    if placement.location_type == SpecificPlantLocation.LOCATION:
        return placement.location
    if placement.location_type == SpecificPlantLocation.SEED_TRAY_CELL:
        return placement.seed_tray_cell.seed_tray.inventory_unit.current_location
    return None


def _plant_targets(workspace, location_ids, scope):
    plants = SpecificPlant.objects.filter(workspace=workspace).select_related(
        'batch__variety__plant',
    ).prefetch_related('locations', 'lifecycle_events')
    if scope.get('variety'):
        plants = plants.filter(batch__variety_id=scope['variety'])
    rows = []
    for plant in plants:
        location = _plant_location(plant)
        if location is None or location.pk not in location_ids:
            continue
        state = plant_lifecycle_summary(plant).state
        snapshot = {
            'location': location.pk, 'state': state, 'batch': plant.batch_id,
            'label_code': _active_code(plant),
            'last_event': plant.lifecycle_events.order_by('-pk').values_list('pk', flat=True).first(),
            'active_placement': plant.locations.filter(ended__isnull=True).values_list('pk', flat=True).first(),
        }
        rows.append(_target(
            StocktakeTarget.TargetType.PLANT, f'plant:{plant.pk}', plant,
            str(plant), location, Decimal('1'), state, snapshot,
        ))
    return rows


SOURCE_BUILDERS = {
    StocktakeTarget.TargetType.LOT: _lot_targets,
    StocktakeTarget.TargetType.SEED_PACKET: _packet_targets,
    StocktakeTarget.TargetType.TRAY: _tray_targets,
    StocktakeTarget.TargetType.COHORT: _cohort_targets,
    StocktakeTarget.TargetType.PLANT: _plant_targets,
}


def scope_rows(workspace, scope):
    """Resolve current authoritative records selected by a scope contract."""
    _location, location_ids = _location_ids(workspace, scope)
    selected = scope.get('target_types') or list(SOURCE_BUILDERS)
    invalid = set(selected) - set(SOURCE_BUILDERS)
    if invalid:
        raise ValidationError({'target_types': f'Unsupported target types: {sorted(invalid)}.'})
    rows = []
    for target_type in selected:
        rows.extend(SOURCE_BUILDERS[target_type](workspace, location_ids, scope))
    return rows


@transaction.atomic
def open_stocktake(workspace, user, scope, *, blind=True, notes=''):
    """Freeze one scope and every expected physical fact at the same instant."""
    rows = scope_rows(workspace, scope)
    if not rows:
        raise ValidationError({'scope': 'The selected scope contains no stock.'})
    now = timezone.now()
    stocktake = Stocktake.objects.create(
        workspace=workspace, status=Stocktake.Status.OPEN,
        counted_at=now, notes=notes, blind=blind, scope=scope,
        scope_digest=_digest(sorted(row['target_key'] for row in rows)),
        created_by=user,
    )
    StocktakeTarget.objects.bulk_create(
        StocktakeTarget(stocktake=stocktake, **row) for row in rows
    )
    return stocktake


@transaction.atomic
def record_count(stocktake, user, target_id, *, counted_quantity=None,
                 observed_location=None, observed_state='', code_snapshot='',
                 resolved_identity=None, notes=''):
    """Append one accepted count attempt without changing source records."""
    stocktake = Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status != Stocktake.Status.OPEN:
        raise ValidationError({'status': 'Resume this stocktake before counting.'})
    target = StocktakeTarget.objects.select_for_update().get(
        pk=target_id, stocktake=stocktake,
    )
    if target.count_status == StocktakeTarget.CountStatus.COUNTED:
        raise ValidationError({'target': 'This target is already counted.'})
    if target.target_type in QUANTITY_TARGETS and counted_quantity is None:
        raise ValidationError({'counted_quantity': 'Enter the physical quantity.'})
    count = StocktakeCount.objects.create(
        target=target, counted_quantity=counted_quantity,
        observed_location=observed_location, observed_state=observed_state,
        code_snapshot=code_snapshot, resolved_identity=resolved_identity or {},
        notes=notes, counter=user,
    )
    target.accepted_count = count
    target.count_status = StocktakeTarget.CountStatus.COUNTED
    target.save(update_fields=['accepted_count', 'count_status', 'updated'])
    return count


def _current_row(stocktake, target):
    rows = SOURCE_BUILDERS[target.target_type](
        stocktake.workspace,
        {target.expected_location_id} if target.expected_location_id else set(),
        {},
    )
    return next((row for row in rows if row['target_key'] == target.target_key), None)


def _variance_rows(target, current):
    count = target.accepted_count
    expected = target.expected_snapshot
    observed = {
        'quantity': str(count.counted_quantity) if count and count.counted_quantity is not None else None,
        'location': count.observed_location_id if count else None,
        'state': count.observed_state if count else '',
        'code': count.code_snapshot if count else '',
    }
    kinds = []
    if target.target_type in QUANTITY_TARGETS:
        if count and count.counted_quantity != target.expected_quantity:
            kinds.append(StocktakeVariance.Kind.QUANTITY)
    elif count is None:
        kinds.append(StocktakeVariance.Kind.MISSING)
    if target.unexpected:
        kinds.append(StocktakeVariance.Kind.EXCESS)
    if count and count.observed_location_id and count.observed_location_id != target.expected_location_id:
        kinds.append(StocktakeVariance.Kind.MISPLACED)
    if count and count.observed_state and count.observed_state != target.expected_state:
        kinds.append(StocktakeVariance.Kind.STATE)
    revision = current['source_revision'] if current else ''
    changed = revision != target.source_revision
    return kinds, expected, observed, changed, revision


@transaction.atomic
def begin_review(stocktake, user):
    """Reveal expected facts and calculate variances without posting corrections."""
    stocktake = Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status not in {Stocktake.Status.OPEN, Stocktake.Status.PAUSED}:
        raise ValidationError({'status': 'Only an open stocktake can enter review.'})
    targets = list(stocktake.targets.select_for_update().select_related('accepted_count'))
    pending_quantities = [
        target.pk for target in targets
        if target.target_type in QUANTITY_TARGETS and target.accepted_count_id is None
    ]
    if pending_quantities:
        raise ValidationError({'targets': f'Count quantity targets before review: {pending_quantities}.'})
    StocktakeVariance.objects.filter(target__stocktake=stocktake).delete()
    for target in targets:
        current = _current_row(stocktake, target)
        kinds, expected, observed, changed, revision = _variance_rows(target, current)
        for kind in kinds:
            StocktakeVariance.objects.create(
                target=target, kind=kind, expected=expected, observed=observed,
                source_changed=changed, current_revision=revision,
            )
    now = timezone.now()
    Stocktake.objects.filter(pk=stocktake.pk).update(
        status=Stocktake.Status.REVIEW, reviewed_by=user,
        reviewed_at=now, updated=now,
    )
    stocktake.refresh_from_db()
    return stocktake


@transaction.atomic
def request_recount(stocktake, target, user, reason):
    """Return one reviewed target to counting while retaining its attempts."""
    if not reason.strip():
        raise ValidationError({'reason': 'Explain why a recount is needed.'})
    stocktake = Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status != Stocktake.Status.REVIEW:
        raise ValidationError({'status': 'Recounts are requested during review.'})
    target = StocktakeTarget.objects.select_for_update().get(pk=target.pk, stocktake=stocktake)
    target.count_status = StocktakeTarget.CountStatus.RECOUNT
    target.save(update_fields=['count_status', 'updated'])
    target.variances.update(
        conflict_resolution=StocktakeVariance.ConflictResolution.RECOUNT,
        conflict_reason=reason, resolved_by=user, resolved_at=timezone.now(),
    )
    Stocktake.objects.filter(pk=stocktake.pk).update(status=Stocktake.Status.OPEN)
    stocktake.refresh_from_db()
    return stocktake


@transaction.atomic
def resolve_variance(variance, user, *, action, reason, payload=None,
                     accept_conflict=False):
    """Record the reviewer's explicit correction and stale-fact decision."""
    variance = StocktakeVariance.objects.select_for_update().select_related(
        'target__stocktake',
    ).get(pk=variance.pk)
    if variance.target.stocktake.status != Stocktake.Status.REVIEW:
        raise ValidationError({'status': 'Resolve variances during review.'})
    if not reason.strip():
        raise ValidationError({'reason': 'Explain the variance resolution.'})
    if variance.source_changed and not accept_conflict:
        raise ValidationError({'conflict': 'Explicitly accept or recount this changed source.'})
    variance.resolution_action = action
    variance.resolution_payload = payload or {}
    variance.resolution_reason = reason
    variance.resolved_by = user
    variance.resolved_at = timezone.now()
    if variance.source_changed:
        variance.conflict_resolution = StocktakeVariance.ConflictResolution.ACCEPTED
        variance.conflict_reason = reason
    variance.save()
    return variance


@transaction.atomic
def approve_stocktake(stocktake, user):
    """Freeze a fully resolved review, enforcing optional counter separation."""
    stocktake = Stocktake.objects.select_for_update().select_related('workspace').get(pk=stocktake.pk)
    if stocktake.status != Stocktake.Status.REVIEW:
        raise ValidationError({'status': 'Only a reviewed stocktake can be approved.'})
    unresolved = stocktake.targets.filter(
        variances__isnull=False, variances__resolved_at__isnull=True,
    ).values_list('variances__pk', flat=True)
    unresolved = list(unresolved)
    if unresolved:
        raise ValidationError({'variances': f'Resolve variances before approval: {unresolved}.'})
    if stocktake.workspace.stocktake_two_person_required:
        counters = stocktake.targets.filter(counts__counter=user).exists()
        if counters:
            raise ValidationError({'reviewer': 'A counter cannot approve this stocktake.'})
    now = timezone.now()
    Stocktake.objects.filter(pk=stocktake.pk).update(
        status=Stocktake.Status.APPROVED, approved_by=user,
        approved_at=now, updated=now,
    )
    stocktake.refresh_from_db()
    return stocktake
