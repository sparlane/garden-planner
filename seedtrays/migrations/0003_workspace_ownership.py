import django.db.models.deletion
import workspaces.models
from django.db import migrations, models
from django.db.models import F


def backfill_and_audit_workspace(apps, _schema_editor):
    """Backfill tray ownership and reject mismatched tray models."""
    tray_model = apps.get_model('seedtrays', 'SeedTrayModel')
    tray = apps.get_model('seedtrays', 'SeedTray')
    tray_model.objects.update(workspace_id=1)
    tray.objects.update(workspace_id=1)
    if tray.objects.exclude(workspace_id=F('model__workspace_id')).exists():
        raise RuntimeError(
            'Seed tray workspace audit failed. Repair trays whose model belongs '
            'to another workspace before retrying the migration.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('seedtrays', '0002_datetimefield_created'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='seedtray',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.AddField(
            model_name='seedtraymodel',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.RunPython(backfill_and_audit_workspace, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='seedtray',
            name='workspace',
            field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.AlterField(
            model_name='seedtraymodel',
            name='workspace',
            field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.AlterField(
            model_name='seedtraymodel',
            name='identifier',
            field=models.CharField(max_length=256),
        ),
        migrations.AddConstraint(
            model_name='seedtraymodel',
            constraint=models.UniqueConstraint(fields=('workspace', 'identifier'), name='unique_tray_model_identifier_workspace'),
        ),
    ]
