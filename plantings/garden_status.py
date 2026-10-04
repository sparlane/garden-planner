"""Audited status changes for aggregate garden plantings."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .batches import lock_batch, validate_batch_for_reopening_a_sowing
from .models import GardenPlanting, GardenPlantingStatusEvent


@transaction.atomic
def finish_garden_planting(planting, user, event_type, occurred_on, reason=''):
    """Finish or fail one active aggregate planting and append the fact."""
    planting = GardenPlanting.objects.select_for_update().get(pk=planting.pk)
    if planting.tracking != GardenPlanting.Tracking.AGGREGATE:
        raise ValidationError('Individual plants use plant lifecycle actions.')
    if planting.finished_on is not None:
        raise ValidationError('This planting is already finished.')
    if event_type not in {
        GardenPlantingStatusEvent.EventType.FINISHED,
        GardenPlantingStatusEvent.EventType.FAILED,
    }:
        raise ValidationError('Select finish or failure.')
    event = GardenPlantingStatusEvent.objects.create(
        workspace=planting.workspace,
        planting=planting,
        event_type=event_type,
        occurred_on=occurred_on,
        reason=reason,
        created_by=user,
    )
    planting.finished_on = occurred_on
    planting.save(update_fields=['finished_on', 'updated'])
    return event


@transaction.atomic
def correct_garden_status(event, user, reason, occurred_on):
    """Reverse a mistaken current finish/failure and reactivate the crop.

    `batch_open_sowings` counts an unfinished aggregate crop as an open sowing,
    so finalizing the batch's output required this crop to be finished.
    Reactivating it afterwards is refused rather than allowed to contradict
    that; `validate_batch_for_reopening_a_sowing` says what it would cost.
    """
    planting = GardenPlanting.objects.select_for_update().get(pk=event.planting_id)
    validate_batch_for_reopening_a_sowing(lock_batch(planting.batch))
    event = GardenPlantingStatusEvent.objects.select_for_update().get(pk=event.pk)
    if hasattr(event, 'reversal'):
        raise ValidationError('That event has already been corrected.')
    if planting.finished_on != event.occurred_on:
        raise ValidationError('Only the current finish or failure can be corrected.')
    correction = GardenPlantingStatusEvent.objects.create(
        workspace=planting.workspace,
        planting=planting,
        event_type=GardenPlantingStatusEvent.EventType.CORRECTED,
        occurred_on=occurred_on,
        reason=reason,
        reversal_of=event,
        created_by=user,
    )
    planting.finished_on = None
    planting.save(update_fields=['finished_on', 'updated'])
    return correction
