import django.db.models.deletion
from django.db import migrations, models


def map_legacy_trays(apps, schema_editor):
    """Create exact serialized opening stock without inventing cost."""
    del schema_editor
    tray_model_model = apps.get_model('seedtrays', 'SeedTrayModel')
    tray_model = apps.get_model('seedtrays', 'SeedTray')
    item_model = apps.get_model('inventory', 'InventoryItem')
    location_model = apps.get_model('inventory', 'InventoryLocation')
    lot_model = apps.get_model('inventory', 'StockLot')
    unit_model = apps.get_model('inventory', 'InventoryUnit')
    movement_model = apps.get_model('inventory', 'StockMovement')

    unknown_locations = {}
    for model in tray_model_model.objects.select_related('workspace').order_by('pk'):
        item = item_model.objects.create(
            workspace_id=model.workspace_id,
            name=f'Tray model: {model.identifier}',
            category='tray',
            base_unit='each',
            tracking_mode='serialized',
            default_usage_basis='manual',
        )
        model.inventory_item_id = item.pk
        model.save(update_fields=['inventory_item'])

    trays = tray_model.objects.select_related(
        'workspace',
        'model__inventory_item',
    ).order_by('pk')
    for tray in trays:
        location = unknown_locations.get(tray.workspace_id)
        if location is None:
            location, _created = location_model.objects.get_or_create(
                workspace_id=tray.workspace_id,
                code='SYSTEM-TRAY-UNKNOWN',
                defaults={
                    'name': 'Unknown tray location',
                    'location_type': 'adjustment',
                    'notes': 'System-managed location for unreconciled legacy trays.',
                },
            )
            unknown_locations[tray.workspace_id] = location
        item = tray.model.inventory_item
        lot = lot_model.objects.create(
            workspace_id=tray.workspace_id,
            item_id=item.pk,
            identifier=f'LEGACY-TRAY-{tray.pk}',
            origin='opening',
            received_on=tray.created.date(),
            initial_base_quantity=1,
            quantity_certainty='exact',
            acquisition_total=None,
            base_unit_cost=None,
            currency_code=tray.workspace.currency_code,
        )
        unit = unit_model.objects.create(
            workspace_id=tray.workspace_id,
            item_id=item.pk,
            source_lot_id=lot.pk,
            asset_code=f'TRAY-LEGACY-{tray.pk}',
            acquisition_cost=None,
            currency_code=tray.workspace.currency_code,
            current_location_id=location.pk,
        )
        movement_model.objects.create(
            workspace_id=tray.workspace_id,
            lot_id=lot.pk,
            unit_id=unit.pk,
            movement_type='opening',
            quantity=1,
            destination_id=location.pk,
            occurred_at=tray.created,
            reason='Legacy tray inventory opening balance.',
        )
        item.stock_history_started_at = tray.created
        item.save(update_fields=['stock_history_started_at'])
        tray.inventory_unit_id = unit.pk
        tray.save(update_fields=['inventory_unit'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_inventoryunitreconciliation_inventoryunit_and_more'),
        ('seedtrays', '0003_workspace_ownership'),
    ]

    operations = [
        migrations.AddField(
            model_name='seedtray',
            name='inventory_unit',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='seed_tray', to='inventory.inventoryunit'),
        ),
        migrations.AddField(
            model_name='seedtraymodel',
            name='inventory_item',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='seed_tray_model', to='inventory.inventoryitem'),
        ),
        migrations.RunPython(map_legacy_trays, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='seedtray',
            name='inventory_unit',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='seed_tray', to='inventory.inventoryunit'),
        ),
        migrations.AlterField(
            model_name='seedtraymodel',
            name='inventory_item',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='seed_tray_model', to='inventory.inventoryitem'),
        ),
    ]
