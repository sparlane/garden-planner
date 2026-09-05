"""Append-only lifecycle commands and projections for direct-sown crops."""

# Public commands spell out their audited request at the boundary.
# pylint: disable=too-many-arguments,too-many-positional-arguments

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from locations.occupancy import check_capacity, plant_contribution

from .models import DirectSownCropEvent, GardenPlanting, SpecificPlant, SpecificPlantLocation
from .lifecycle import record_germination_event


LOSS_TYPES = {
    DirectSownCropEvent.EventType.THINNED,
    DirectSownCropEvent.EventType.FAILED_GERMINATION,
    DirectSownCropEvent.EventType.PEST_LOSS,
    DirectSownCropEvent.EventType.REMOVED,
    DirectSownCropEvent.EventType.INDIVIDUALIZED,
}
LIVING_REMOVAL_TYPES = LOSS_TYPES - {
    DirectSownCropEvent.EventType.FAILED_GERMINATION,
}


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def _effective_events(planting):
    prefetched = getattr(planting, '_prefetched_objects_cache', {}).get('direct_sown_events')
    if prefetched is not None:
        return list(prefetched)
    return list(
        planting.direct_sown_events.select_related(
            'reversal', 'garden_square_before', 'location_before',
            'garden_square_after', 'location_after',
        ).order_by('occurred_on', 'pk')
    )


def direct_sown_summary(planting):  # pylint: disable=too-many-branches
    """Replay effective facts into counts and the crop's current location."""
    events = _effective_events(planting)
    reversed_ids = {
        event.reversal_of_id for event in events
        if event.event_type == DirectSownCropEvent.EventType.REVERSED
    }
    current = None
    emerged = 0
    losses = {
        event_type: 0 for event_type in LOSS_TYPES
        if event_type != DirectSownCropEvent.EventType.INDIVIDUALIZED
    }
    individualized = 0
    quality = None
    square = planting.garden_square
    location = planting.location
    for event in events:
        if event.pk in reversed_ids or event.event_type == DirectSownCropEvent.EventType.REVERSED:
            continue
        if event.event_type == DirectSownCropEvent.EventType.EMERGED:
            if event.quantity is not None:
                current = (current or 0) + event.quantity
                emerged += event.quantity
                if event.count_quality == DirectSownCropEvent.CountQuality.ESTIMATED:
                    quality = event.count_quality
                elif quality is None:
                    quality = event.count_quality
        elif event.event_type == DirectSownCropEvent.EventType.RETAINED:
            if event.quantity is not None:
                current = event.quantity
                quality = event.count_quality
        elif event.event_type in LOSS_TYPES:
            if event.event_type in LIVING_REMOVAL_TYPES:
                current = None if current is None else current - event.quantity
            if event.event_type == DirectSownCropEvent.EventType.INDIVIDUALIZED:
                individualized += event.quantity
            else:
                losses[event.event_type] += event.quantity
        elif event.event_type == DirectSownCropEvent.EventType.MOVED:
            square = event.garden_square_after
            location = event.location_after
    harvests = planting.batch.harvests.filter(status='posted')
    return {
        'seeds_sown': planting.quantity,
        'emerged_plants': emerged if any(
            all((
                event.event_type == DirectSownCropEvent.EventType.EMERGED,
                event.quantity is not None,
                event.pk not in reversed_ids,
            ))
            for event in events
        ) else None,
        'losses': losses,
        'loss_quantity': sum(losses.values()),
        'individualized': individualized,
        'current_plants': current,
        'count_quality': quality,
        'state': 'unknown' if current is None else ('depleted' if current == 0 else 'growing'),
        'garden_square': square,
        'location': location,
        'harvest': [
            {'id': item.pk, 'quantity': item.quantity, 'unit_code': item.unit_code}
            for item in harvests.order_by('harvested_at', 'pk')
        ],
        'events': events,
    }


def _locked(planting):
    locked = GardenPlanting.objects.select_for_update().select_related(
        'workspace', 'garden_square', 'location', 'batch',
    ).get(pk=planting.pk)
    if locked.source != GardenPlanting.Source.DIRECT_SEED or (
        locked.tracking != GardenPlanting.Tracking.AGGREGATE
    ):
        raise ValidationError({'planting': 'Choose an aggregate direct-sown crop.'})
    if locked.finished_on is not None:
        raise ValidationError({'planting': 'This crop is already finished.'})
    return locked


@transaction.atomic
def record_direct_sown_event(planting, user, event_type, occurred_on, quantity=None,
                             count_quality='', notes=''):
    """Append a count fact after validating it against locked current state."""
    planting = _locked(planting)
    summary = direct_sown_summary(planting)
    delta = 0
    if event_type == DirectSownCropEvent.EventType.EMERGED:
        delta = quantity or 0
    elif event_type == DirectSownCropEvent.EventType.RETAINED:
        if quantity is not None and summary['current_plants'] is not None:
            delta = quantity - summary['current_plants']
    elif event_type == DirectSownCropEvent.EventType.FAILED_GERMINATION:
        known_failed = summary['losses'][event_type]
        if quantity is None or quantity > planting.quantity - (summary['emerged_plants'] or 0) - known_failed:
            raise ValidationError({'quantity': 'This exceeds the seeds not recorded as emerged.'})
    elif event_type in LIVING_REMOVAL_TYPES:
        if summary['current_plants'] is None:
            raise ValidationError({'quantity': 'Record a numeric emergence or retained count first.'})
        if quantity is None or quantity > summary['current_plants']:
            raise ValidationError({'quantity': 'This would make the living count negative.'})
        delta = -quantity
    else:
        raise ValidationError({'event_type': 'Select a quantity lifecycle event.'})
    return DirectSownCropEvent.objects.create(
        workspace=planting.workspace, planting=planting, event_type=event_type,
        occurred_on=occurred_on, quantity=quantity, quantity_delta=delta,
        count_quality=count_quality, notes=notes, created_by=_actor(user),
    )


@transaction.atomic
def move_direct_sown_crop(planting, user, occurred_on, garden_square=None,
                          location=None, notes='', override_reason=''):
    """Move one aggregate crop while preserving its original placement."""
    planting = _locked(planting)
    summary = direct_sown_summary(planting)
    if bool(garden_square) == bool(location):
        raise ValidationError({'location': 'Select exactly one destination.'})
    if location is not None and summary['current_plants'] is not None:
        for _index in range(summary['current_plants']):
            check_capacity(location, plant_contribution(), override_reason)
    return DirectSownCropEvent.objects.create(
        workspace=planting.workspace, planting=planting,
        event_type=DirectSownCropEvent.EventType.MOVED, occurred_on=occurred_on,
        garden_square_before=summary['garden_square'], location_before=summary['location'],
        garden_square_after=garden_square, location_after=location,
        notes=notes, created_by=_actor(user),
    )


@transaction.atomic
def individualize_direct_sown_crop(planting, user, quantity, occurred_on, names,
                                   notes='', override_reason=''):
    """Remove exact aggregate quantity and create the same number of identities."""
    planting = _locked(planting)
    summary = direct_sown_summary(planting)
    if summary['current_plants'] is None:
        raise ValidationError({'quantity': 'Record a numeric emergence or retained count first.'})
    if quantity < 1 or quantity > summary['current_plants']:
        raise ValidationError({'quantity': 'This would make the living count negative.'})
    event = DirectSownCropEvent.objects.create(
        workspace=planting.workspace, planting=planting,
        event_type=DirectSownCropEvent.EventType.INDIVIDUALIZED,
        occurred_on=occurred_on, quantity=quantity, quantity_delta=-quantity,
        notes=notes, created_by=_actor(user),
    )
    started = timezone.make_aware(
        datetime.combine(occurred_on, time.min), ZoneInfo(planting.workspace.timezone),
    )
    plants = []
    for index in range(quantity):
        if summary['location'] is not None:
            check_capacity(summary['location'], plant_contribution(), override_reason)
        plant = SpecificPlant.objects.create(
            workspace=planting.workspace, garden_planting=planting,
            germinated=started, name=names[index].strip() if index < len(names) else '',
            notes=notes,
        )
        record_germination_event(plant, user, notes)
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=(
                SpecificPlantLocation.GARDEN_SQUARE
                if summary['garden_square'] is not None else SpecificPlantLocation.LOCATION
            ),
            garden_square=summary['garden_square'], location=summary['location'],
            started=started, notes=notes, override_reason=override_reason,
        )
        plants.append(plant)
    return event, plants


@transaction.atomic
def reverse_direct_sown_event(event, user, occurred_on, notes):
    """Append a compensating marker; projections omit the original fact."""
    planting = _locked(event.planting)
    event = DirectSownCropEvent.objects.select_for_update().get(pk=event.pk)
    if hasattr(event, 'reversal'):
        raise ValidationError({'event': 'That event has already been reversed.'})
    if event.event_type == DirectSownCropEvent.EventType.INDIVIDUALIZED:
        raise ValidationError({'event': 'Individualization must be corrected on the plant records.'})
    return DirectSownCropEvent.objects.create(
        workspace=planting.workspace, planting=planting,
        event_type=DirectSownCropEvent.EventType.REVERSED, occurred_on=occurred_on,
        quantity_delta=-event.quantity_delta, reversal_of=event,
        notes=notes, created_by=_actor(user),
    )
