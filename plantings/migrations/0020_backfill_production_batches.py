"""Give every historical sowing its own deterministic production batch."""

from django.db import migrations


SOWING_MODELS = (
    ('GardenRowDirectSowPlanting', 'LEGACY-ROW'),
    ('GardenSquareDirectSowPlanting', 'LEGACY-SQUARE'),
    ('SeedTrayPlanting', 'LEGACY-TRAY'),
)

MIGRATION_REASON = 'Migrated from existing sowing.'


def _packet_repairs(planting):
    """Return repair notes for a sowing's seed packet relationships."""
    packet = planting.seeds_used
    if packet.workspace_id != planting.workspace_id:
        return [
            f'Seed packet #{packet.pk} belongs to workspace '
            f'{packet.workspace_id}, not workspace {planting.workspace_id}. '
            'Reassign the packet or the sowing before relying on this batch.'
        ]
    if packet.seeds.workspace_id != planting.workspace_id:
        return [
            f'Seed product #{packet.seeds_id} belongs to workspace '
            f'{packet.seeds.workspace_id}, not workspace '
            f'{planting.workspace_id}. Reassign the seed product before '
            'relying on this batch variety.'
        ]
    return []


def _direct_sow_repairs(planting):
    """Return repair notes for a direct sowing's garden location."""
    location = planting.location
    if location.workspace_id != planting.workspace_id:
        return [
            f'Location #{location.pk} belongs to workspace '
            f'{location.workspace_id}, not workspace {planting.workspace_id}. '
            'Move the sowing to a location in its own workspace.'
        ]
    return []


def _tray_repairs(planting):
    """Return repair notes for a tray sowing's tray and cell allocations."""
    repairs = []
    cell_plantings = list(planting.cell_plantings.select_related('cell'))
    tray = planting.seed_tray
    if tray is None:
        if cell_plantings:
            repairs.append(
                f'Sowing #{planting.pk} allocates {len(cell_plantings)} cells '
                'but has no seed tray. Attach the sowing to the tray that '
                'owns those cells.'
            )
        return repairs

    if tray.workspace_id != planting.workspace_id:
        repairs.append(
            f'Seed tray #{tray.pk} belongs to workspace {tray.workspace_id}, '
            f'not workspace {planting.workspace_id}. Move the sowing to a '
            'tray in its own workspace.'
        )
    stray_cells = sorted(
        cell_planting.cell_id
        for cell_planting in cell_plantings
        if cell_planting.cell.tray_id != tray.pk
    )
    if stray_cells:
        repairs.append(
            f'Cells {stray_cells} are not part of seed tray #{tray.pk}. '
            'Reallocate them to cells of the sowing tray.'
        )
    return repairs


def _repairs_for(planting, is_tray):
    """Collect every actionable repair note for one historical sowing."""
    repairs = _packet_repairs(planting)
    if is_tray:
        repairs.extend(_tray_repairs(planting))
    else:
        repairs.extend(_direct_sow_repairs(planting))
    return repairs


def create_legacy_batches(apps, _schema_editor):
    """Create one active batch per historical sowing and link it."""
    batch_model = apps.get_model('plantings', 'ProductionBatch')
    transition_model = apps.get_model('plantings', 'ProductionBatchTransition')

    for model_name, code_prefix in SOWING_MODELS:
        is_tray = model_name == 'SeedTrayPlanting'
        queryset = apps.get_model('plantings', model_name).objects.filter(
            batch__isnull=True,
        ).select_related('seeds_used__seeds').order_by('pk')
        for planting in queryset:
            repairs = _repairs_for(planting, is_tray)
            batch, created = batch_model.objects.get_or_create(
                workspace_id=planting.workspace_id,
                code=f'{code_prefix}-{planting.pk}',
                defaults={
                    'variety_id': planting.seeds_used.seeds.plant_variety_id,
                    'status': 'active',
                    'actual_start': planting.planted,
                    'notes': '',
                    'created_by': None,
                    'repair_state': 'needs_repair' if repairs else 'none',
                    'repair_details': '\n'.join(repairs),
                },
            )
            if created:
                transition_model.objects.create(
                    batch=batch,
                    previous_status='',
                    new_status='active',
                    created_by=None,
                    reason=MIGRATION_REASON,
                )
            planting.batch = batch
            planting.save(update_fields=['batch'])


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0019_productionbatch'),
    ]

    operations = [
        migrations.RunPython(create_legacy_batches, migrations.RunPython.noop),
    ]
