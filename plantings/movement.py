"""Atomic plant movement and location-history validation shared by callers.

Validation retains the existing DRF error contract used by movement callers.
This module has no dependency on views, serializers, or router registration.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from inventory.models import InventoryUnit, StockLot
from seedtrays.models import SeedTrayGeneration
from locations.occupancy import Occupancy, check_capacity, plant_contribution

from .lifecycle import record_transplant_event
from .models import SpecificPlant, SpecificPlantLocation


def _model_errors(error):
    """Translate Django validation into field-friendly REST errors."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


FIELD_MISSING = object()


def lock_placement_containers(placements=(), destinations=()):
    """Take every source and destination pot lock before any move changes a fill."""
    unit_ids = {row.container_unit_id for row in placements if row.container_unit_id}
    fill_ids = {row.container_fill_id for row in placements if row.container_fill_id}
    for data in destinations:
        if data.get('container_unit'):
            unit_ids.add(data['container_unit'].pk)
        if data.get('container_fill'):
            fill_ids.add(data['container_fill'].pk)
    fills = list(SeedTrayGeneration.objects.filter(pk__in=fill_ids))
    unit_ids.update(fill.inventory_unit_id for fill in fills if fill.inventory_unit_id)
    lot_ids = {fill.stock_lot_id for fill in fills if fill.stock_lot_id}
    list(StockLot.objects.select_for_update().filter(pk__in=lot_ids).order_by('pk'))
    list(InventoryUnit.objects.select_for_update(of=('self',)).filter(pk__in=unit_ids).order_by('pk'))
    list(SeedTrayGeneration.objects.select_for_update().filter(pk__in=fill_ids).order_by('pk'))


def places_from(data):
    """Read every kind of place a plant can be out of request data.

    Driven by the model's own field table so that adding a fourth kind of place
    reaches the API without a second list needing to be remembered.
    """
    return {
        field_name: data.get(field_name, FIELD_MISSING)
        for field_name in SpecificPlantLocation.LOCATION_FIELDS.values()
    }


def validate_specific_plant_location(
    *,
    location_type=None,
    places=None,
    interval=None,
    instance=None,
):
    """
    Validate location fields, optionally defaulting omitted fields from an instance.
    """
    supplied = dict(places or {})
    if instance is not None:
        if location_type is FIELD_MISSING:
            location_type = instance.location_type
        for field_name in SpecificPlantLocation.LOCATION_FIELDS.values():
            if supplied.get(field_name, FIELD_MISSING) is FIELD_MISSING:
                supplied[field_name] = getattr(instance, field_name)
        if interval is None:
            interval = (instance.started, instance.ended)

    location_data = {
        'location_type': None if location_type is FIELD_MISSING else location_type,
    }
    for field_name in SpecificPlantLocation.LOCATION_FIELDS.values():
        value = supplied.get(field_name, FIELD_MISSING)
        location_data[field_name] = None if value is FIELD_MISSING else value
    if interval is not None:
        location_data['started'], location_data['ended'] = interval

    tmp = SpecificPlantLocation(
        **location_data,
    )
    try:
        tmp.clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict) from exc


def validate_location_history(
    *,
    specific_plant,
    started,
    ended,
    exclude_pk=None,
    append_only=False,
):
    """Reject location intervals that overlap or insert before existing history."""
    locations = SpecificPlantLocation.objects.filter(specific_plant=specific_plant)
    if exclude_pk is not None:
        locations = locations.exclude(pk=exclude_pk)

    if append_only:
        latest_location = locations.order_by('-started', '-pk').first()
        if latest_location is None:
            return
        if latest_location.ended is None or started < latest_location.ended:
            raise ValidationError({
                'started': 'New locations must start at or after existing history ends.'
            })
        return

    overlapping = locations.filter(Q(ended__isnull=True) | Q(ended__gt=started))
    if ended is not None:
        overlapping = overlapping.filter(started__lt=ended)
    if overlapping.exists():
        raise ValidationError({
            'started': 'Location interval overlaps another location.'
        })


def get_single_active_location_for_update(plant):
    """
    Lock and return the current active location for a plant.
    """
    active_locations = list(
        SpecificPlantLocation.objects
        .select_for_update()
        .filter(specific_plant=plant, ended__isnull=True)
    )
    if len(active_locations) > 1:
        raise ValidationError({
            'specific_plant': 'Plant has multiple active locations.'
        })
    if active_locations:
        return active_locations[0]
    return None


def is_active_location_integrity_error(exc):
    """
    Return whether an integrity error came from the active-location constraint.
    """
    cause = getattr(exc, '__cause__', None)
    diag = getattr(cause, 'diag', None)
    if getattr(diag, 'constraint_name', None) == 'unique_active_location_per_plant':
        return True

    message = ' '.join(str(arg) for arg in exc.args)
    names_constraint = 'unique_active_location_per_plant' in message
    names_sqlite_column = 'plantings_specificplantlocation.specific_plant_id' in message
    return names_constraint or names_sqlite_column


def _check_destination_capacity(destination, override_reason, plant, fill=None):
    """Refuse a bench that is full, or that cannot measure a single plant.

    Locks the destination and every capacitated ancestor before counting, so
    two plants racing for the last space cannot both read it as free.
    """
    if not destination.active:
        raise ValidationError({'location': 'The location is inactive.'})
    try:
        contribution = plant_contribution(plant)
        if fill is not None and fill.stock_lot_id:
            contribution = Occupancy(plants=1, containers=1, area=fill.stock_lot.item.container_footprint_m2 or 0)
        check_capacity(destination, contribution, override_reason)
    except DjangoValidationError as exc:
        raise ValidationError(
            {'location': _model_errors(exc).get('destination', exc.messages)},
        ) from exc


def move_specific_plant(plant, move_data, user=None):
    """
    Move a plant by ending its active location and creating the new one atomically.

    A move into a garden square is also the moment the plant is planted out, so
    the matching lifecycle fact is appended in the same transaction. Only a
    garden square counts: moving a plant onto a nursery bench is still nursery
    work, and calling it planting out would close a production batch early.
    """
    started = move_data.get('started') or timezone.now()
    move_payload = {**move_data, 'started': started}
    planted_out = move_payload.get('location_type') == SpecificPlantLocation.GARDEN_SQUARE
    destination = move_payload.get('location')
    with transaction.atomic():
        plant = get_object_or_404(
            SpecificPlant.objects.select_for_update(),
            pk=plant.pk,
            workspace=plant.workspace,
        )
        if destination is not None:
            _check_destination_capacity(
                destination, move_payload.get('override_reason', ''), plant, move_payload.get('container_fill'),
            )
        if planted_out:
            try:
                record_transplant_event(plant, user, started)
            except DjangoValidationError as exc:
                raise ValidationError(_model_errors(exc)) from exc
        active_location = get_single_active_location_for_update(plant)

        # Lock both sources and destinations: lots, units, then fills. Opposite
        # moves cannot each hold their source while waiting for a destination.
        lock_placement_containers([active_location] if active_location else [], [move_payload])

        if active_location:
            if started < active_location.started:
                raise ValidationError({
                    'started': 'Move cannot start before the active location.'
                })
            active_location.ended = started
            try:
                active_location.save(update_fields=['ended'])
            except DjangoValidationError as exc:
                raise ValidationError(_model_errors(exc)) from exc
        else:
            validate_location_history(
                specific_plant=plant,
                started=started,
                ended=None,
                append_only=True,
            )

        try:
            return SpecificPlantLocation.objects.create(
                specific_plant=plant,
                **move_payload,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        except IntegrityError as exc:
            if not is_active_location_integrity_error(exc):
                raise
            raise ValidationError({
                'specific_plant': 'Move must leave exactly one active location.'
            }) from exc
