import django.db.models.deletion
import workspaces.models
from django.db import migrations, models


def backfill_workspace(apps, _schema_editor):
    apps.get_model('supplies', 'Supplier').objects.update(workspace_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ('supplies', '0001_initial'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
        migrations.RunPython(backfill_workspace, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='supplier',
            name='workspace',
            field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
        ),
    ]
