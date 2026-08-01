import django.db.models.deletion
import workspaces.models
from django.db import migrations, models
from django.db.models import F


OWNED_MODELS = ('PlantFamily', 'Plant', 'PlantVariety')


def backfill_and_audit_workspace(apps, _schema_editor):
    """Backfill ownership and reject inconsistent catalog parentage."""
    for model_name in OWNED_MODELS:
        apps.get_model('plants', model_name).objects.update(workspace_id=1)

    plant_model = apps.get_model('plants', 'Plant')
    variety_model = apps.get_model('plants', 'PlantVariety')
    invalid_plants = plant_model.objects.exclude(workspace_id=F('family__workspace_id'))
    invalid_varieties = variety_model.objects.exclude(workspace_id=F('plant__workspace_id'))
    if invalid_plants.exists() or invalid_varieties.exists():
        raise RuntimeError(
            'Plant workspace audit failed. Repair cross-workspace family, plant, '
            'or variety relationships before retrying the migration.'
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
        ('plants', '0001_initial'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        *(add_workspace(name.lower()) for name in OWNED_MODELS),
        migrations.RunPython(backfill_and_audit_workspace, migrations.RunPython.noop),
        *(require_workspace(name.lower()) for name in OWNED_MODELS),
    ]
