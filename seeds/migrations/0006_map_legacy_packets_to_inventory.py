"""Create unreconciled inventory identities for existing seed packets."""

from django.db import migrations


def map_legacy_packets(apps, schema_editor):
    """Create seed items and unknown opening lots without inventing stock."""
    del schema_editor
    seeds_model = apps.get_model('seeds', 'Seeds')
    packet_model = apps.get_model('seeds', 'SeedPacket')
    item_model = apps.get_model('inventory', 'InventoryItem')
    location_model = apps.get_model('inventory', 'InventoryLocation')
    lot_model = apps.get_model('inventory', 'StockLot')

    for seeds in seeds_model.objects.select_related('workspace').order_by('pk'):
        item = item_model.objects.create(
            workspace_id=seeds.workspace_id,
            name=f'Seed catalog {seeds.pk}',
            category='seed',
            base_unit='seed',
            tracking_mode='lot',
            default_usage_basis='manual',
        )
        seeds.inventory_item_id = item.pk
        seeds.save(update_fields=['inventory_item'])

    packets = packet_model.objects.select_related(
        'workspace',
        'seeds__inventory_item',
    ).order_by('pk')
    for packet in packets:
        location = location_model.objects.create(
            workspace_id=packet.workspace_id,
            name=f'Seed packet {packet.pk}',
            code=f'SEED-PACKET-{packet.pk}',
            location_type='seed_packet',
            notes='System-managed legacy seed packet container.',
        )
        lot = lot_model.objects.create(
            workspace_id=packet.workspace_id,
            item_id=packet.seeds.inventory_item_id,
            identifier=f'LEGACY-PACKET-{packet.pk}',
            origin='opening',
            received_on=packet.purchase_date,
            expires_on=packet.sow_by,
            initial_base_quantity=None,
            quantity_certainty='unknown',
            acquisition_total=None,
            base_unit_cost=None,
            currency_code=packet.workspace.currency_code,
        )
        packet.storage_location_id = location.pk
        packet.stock_lot_id = lot.pk
        packet.save(update_fields=['storage_location', 'stock_lot'])


class Migration(migrations.Migration):
    """Map legacy seed records after inventory supports unknown quantities."""

    dependencies = [
        ('inventory', '0003_remove_stocklot_inventory_lot_positive_initial_quantity_and_more'),
        ('seeds', '0005_seedpacket_stock_lot_seedpacket_storage_location_and_more'),
    ]

    operations = [
        migrations.RunPython(map_legacy_packets, migrations.RunPython.noop),
    ]
