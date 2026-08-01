import django.db.models.deletion
import workspaces.models
from django.db import migrations, models
from django.db.models import F


def backfill_and_audit_workspace(apps, _schema_editor):
    """Backfill ownership and reject inconsistent seed catalog parentage."""
    seeds_model = apps.get_model('seeds', 'Seeds')
    packet_model = apps.get_model('seeds', 'SeedPacket')
    seeds_model.objects.update(workspace_id=1)
    packet_model.objects.update(workspace_id=1)

    invalid_seeds = seeds_model.objects.exclude(
        workspace_id=F('supplier__workspace_id'),
    ) | seeds_model.objects.exclude(
        workspace_id=F('plant_variety__workspace_id'),
    )
    invalid_packets = packet_model.objects.exclude(
        workspace_id=F('seeds__workspace_id'),
    )
    if invalid_seeds.exists() or invalid_packets.exists():
        raise RuntimeError(
            'Seed workspace audit failed. Repair cross-workspace supplier, '
            'variety, seed, or packet relationships before retrying the migration.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('plants', '0002_workspace_ownership'),
        ('seeds', '0003_alter_seeds_supplier_delete_supplier'),
        ('supplies', '0002_supplier_workspace'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='seedpacket',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.AddField(
            model_name='seeds',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.RunPython(backfill_and_audit_workspace, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='seedpacket',
            name='workspace',
            field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.AlterField(
            model_name='seeds',
            name='workspace',
            field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
    ]
