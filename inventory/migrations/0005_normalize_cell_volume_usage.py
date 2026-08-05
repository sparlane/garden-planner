"""Normalize cell-volume items to derive usage from tray measurements."""

from django.db import migrations


def normalize_cell_volume_usage(apps, _schema_editor):
    """Remove obsolete rates and retain only convertible volume items."""
    inventory_item = apps.get_model('inventory', 'InventoryItem')
    cell_volume_items = inventory_item.objects.filter(
        default_usage_basis='cell_volume',
    )
    cell_volume_items.exclude(base_unit__in=('ml', 'l')).update(
        default_usage_basis='manual',
        default_usage_rate=None,
        usage_rate_unit=None,
    )
    cell_volume_items.filter(base_unit__in=('ml', 'l')).update(
        default_usage_rate=None,
        usage_rate_unit=None,
    )


class Migration(migrations.Migration):
    """Discard rates that duplicated or obscured tray cell volumes."""

    dependencies = [
        ('inventory', '0004_inventoryunitreconciliation_inventoryunit_and_more'),
    ]

    operations = [
        migrations.RunPython(
            normalize_cell_volume_usage,
            migrations.RunPython.noop,
        ),
    ]
