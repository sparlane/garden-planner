import django.db.models.deletion
import workspaces.models
from django.db import migrations, models
from django.db.models import F, Q


OWNED_MODELS = (
    'GardenRowDirectSowPlanting',
    'GardenSquareDirectSowPlanting',
    'GardenSquareTransplant',
    'SeedTrayPlanting',
    'SpecificPlant',
)


def backfill_and_audit_workspace(apps, _schema_editor):
    """Backfill ownership and reject inconsistent cultivation parentage."""
    for model_name in OWNED_MODELS:
        apps.get_model('plantings', model_name).objects.update(workspace_id=1)

    relationships = (
        ('GardenRowDirectSowPlanting', 'seeds_used__workspace_id'),
        ('GardenRowDirectSowPlanting', 'location__workspace_id'),
        ('GardenSquareDirectSowPlanting', 'seeds_used__workspace_id'),
        ('GardenSquareDirectSowPlanting', 'location__workspace_id'),
        ('GardenSquareTransplant', 'original_planting__workspace_id'),
        ('GardenSquareTransplant', 'location__workspace_id'),
        ('SpecificPlant', 'cell_planting__seed_tray_planting__workspace_id'),
    )
    for model_name, parent_workspace in relationships:
        invalid = apps.get_model('plantings', model_name).objects.exclude(
            workspace_id=F(parent_workspace),
        )
        if invalid.exists():
            raise RuntimeError(
                'Planting workspace audit failed. Repair cross-workspace seed, '
                'location, tray, or planting relationships before retrying.'
            )

    tray_planting = apps.get_model('plantings', 'SeedTrayPlanting')
    invalid_tray_plantings = tray_planting.objects.filter(
        ~Q(workspace_id=F('seeds_used__workspace_id')) |
        (
            Q(seed_tray__isnull=False) &
            ~Q(workspace_id=F('seed_tray__workspace_id'))
        ),
    )
    if invalid_tray_plantings.exists():
        raise RuntimeError(
            'Planting workspace audit failed. Repair cross-workspace seed packet '
            'or seed tray relationships before retrying.'
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
        ('garden', '0005_workspace_ownership'),
        ('plantings', '0016_audit_transplant_ownership'),
        ('seeds', '0004_workspace_ownership'),
        ('seedtrays', '0003_workspace_ownership'),
        ('workspaces', '0001_initial'),
    ]

    operations = [
        *(add_workspace(name.lower()) for name in OWNED_MODELS),
        migrations.RunPython(backfill_and_audit_workspace, migrations.RunPython.noop),
        *(require_workspace(name.lower()) for name in OWNED_MODELS),
    ]
