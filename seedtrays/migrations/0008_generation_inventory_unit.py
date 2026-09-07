"""Give existing fills their physical container identity without recosting."""

import django.db.models.deletion
from django.db import migrations, models


def link_containers(apps, schema_editor):
    """Copy the recorded tray identity; never save or recalculate a fill."""
    fills = apps.get_model('seedtrays', 'SeedTrayGeneration')
    trays = apps.get_model('seedtrays', 'SeedTray')
    alias = schema_editor.connection.alias
    unit = trays.objects.using(alias).filter(pk=models.OuterRef('tray_id')).values('inventory_unit_id')[:1]
    fills.objects.using(alias).update(inventory_unit_id=models.Subquery(unit))


class Migration(migrations.Migration):

    dependencies = [
        ('seedtrays', '0007_retire_tray_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='seedtraygeneration',
            name='inventory_unit',
            field=models.ForeignKey(
                editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='container_fills', to='inventory.inventoryunit',
            ),
        ),
        migrations.RunPython(link_containers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='seedtraygeneration',
            name='inventory_unit',
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='container_fills', to='inventory.inventoryunit',
            ),
        ),
    ]
