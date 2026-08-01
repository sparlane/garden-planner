import django.db.models.deletion
import workspaces.models
from django.db import migrations, models
from django.db.models import F


OWNED_MODELS = ('GardenArea', 'GardenBed', 'GardenRow', 'GardenSquare')


def backfill_and_audit_workspace(apps, _schema_editor):
    """Backfill ownership and reject inconsistent garden parentage."""
    for model_name in OWNED_MODELS:
        apps.get_model('garden', model_name).objects.update(workspace_id=1)

    relationships = (
        ('GardenBed', 'area__workspace_id'),
        ('GardenRow', 'bed__workspace_id'),
        ('GardenSquare', 'bed__workspace_id'),
    )
    for model_name, parent_workspace in relationships:
        invalid = apps.get_model('garden', model_name).objects.exclude(
            workspace_id=F(parent_workspace),
        )
        if invalid.exists():
            raise RuntimeError(
                'Garden workspace audit failed. Repair cross-workspace area, '
                'bed, row, or square relationships before retrying the migration.'
            )


def add_workspace(model_name):
    return migrations.AddField(
        model_name=model_name,
        name='workspace',
        field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
    )


def require_workspace(model_name):
    return migrations.AlterField(
        model_name=model_name,
        name='workspace',
        field=models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('garden', '0004_constrain_garden_geometry'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        *(add_workspace(name.lower()) for name in OWNED_MODELS),
        migrations.RunPython(backfill_and_audit_workspace, migrations.RunPython.noop),
        *(require_workspace(name.lower()) for name in OWNED_MODELS),
    ]
