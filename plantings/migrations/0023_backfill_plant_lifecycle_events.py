"""Give every existing plant the lifecycle facts already recorded elsewhere.

Only germination and planting out are trustworthy in the historical data. A
`removed` flag or an ended location says a planting activity finished, not that
a plant was sold, failed, or harvested, so no final outcome is invented here:
those plants stay active until an operator records what became of them.
"""

from django.db import migrations


GERMINATED = 'germinated'
TRANSPLANTED = 'transplanted'
GARDEN_SQUARE = 'garden_square'


def _plant_origins(SpecificPlant):
    """Map each plant to the workspace, batch, and time that produced it."""
    return {
        plant_id: (workspace_id, batch_id, germinated)
        for plant_id, workspace_id, batch_id, germinated in SpecificPlant.objects.values_list(
            'pk',
            'workspace_id',
            'cell_planting__seed_tray_planting__batch_id',
            'germinated',
        )
    }


def backfill_lifecycle_events(apps, _schema_editor):
    """Record one germination per plant and one transplant per planting out."""
    SpecificPlant = apps.get_model('plantings', 'SpecificPlant')
    SpecificPlantLocation = apps.get_model('plantings', 'SpecificPlantLocation')
    PlantLifecycleEvent = apps.get_model('plantings', 'PlantLifecycleEvent')

    origins = _plant_origins(SpecificPlant)
    events = [
        PlantLifecycleEvent(
            workspace_id=workspace_id,
            plant_id=plant_id,
            batch_id=batch_id,
            event_type=GERMINATED,
            occurred_at=germinated,
            reason='Backfilled from the recorded germination.',
        )
        for plant_id, (workspace_id, batch_id, germinated) in sorted(origins.items())
    ]

    planted_out = SpecificPlantLocation.objects.filter(
        location_type=GARDEN_SQUARE,
    ).order_by('specific_plant_id', 'started', 'pk')
    for plant_id, started in planted_out.values_list('specific_plant_id', 'started'):
        if plant_id not in origins:
            continue
        workspace_id, batch_id, germinated = origins[plant_id]
        events.append(PlantLifecycleEvent(
            workspace_id=workspace_id,
            plant_id=plant_id,
            batch_id=batch_id,
            event_type=TRANSPLANTED,
            # Clamp so a location that predates its own germination cannot make
            # the replayed history run backwards.
            occurred_at=max(started, germinated),
            reason='Backfilled from the recorded location history.',
        ))

    PlantLifecycleEvent.objects.bulk_create(events)


def remove_lifecycle_events(apps, _schema_editor):
    """Drop the backfilled history so the migration can be unapplied."""
    PlantLifecycleEvent = apps.get_model('plantings', 'PlantLifecycleEvent')
    PlantLifecycleEvent.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0022_plantlifecycleevent'),
    ]

    operations = [
        migrations.RunPython(backfill_lifecycle_events, remove_lifecycle_events),
    ]
