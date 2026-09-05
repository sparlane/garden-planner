"""Give existing Garden workspaces the shared health catalogs."""

from django.db import migrations


OBSERVATION_TYPES = (
    ('pest-signs', 'Pest signs'),
    ('disease-symptoms', 'Disease symptoms'),
    ('physical-damage', 'Physical damage'),
    ('vigor-stress', 'Vigor or stress'),
    ('environmental', 'Environmental issue'),
)

DIAGNOSES = (
    ('unknown-pest', 'Unknown pest', 'pest'),
    ('unknown-disease', 'Unknown disease', 'disease'),
    ('physical-damage', 'Physical damage', 'damage'),
    ('low-vigor', 'Low vigor', 'vigor'),
    ('environmental-stress', 'Environmental stress', 'other'),
)


def seed_garden_catalogs(apps, _schema_editor):
    """Make problem reporting immediately usable in existing gardens."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    ObservationType = apps.get_model('health', 'HealthObservationType')
    Diagnosis = apps.get_model('health', 'HealthDiagnosis')
    for workspace in Workspace.objects.filter(mode='garden'):
        for order, (code, name) in enumerate(OBSERVATION_TYPES):
            ObservationType.objects.get_or_create(
                workspace=workspace, code=code,
                defaults={'name': name, 'display_order': order},
            )
        for order, (code, name, category) in enumerate(DIAGNOSES):
            Diagnosis.objects.get_or_create(
                workspace=workspace, code=code,
                defaults={
                    'name': name, 'category': category,
                    'display_order': order,
                },
            )


class Migration(migrations.Migration):
    dependencies = [('health', '0004_backfill_released_quarantine')]
    operations = [migrations.RunPython(
        seed_garden_catalogs, migrations.RunPython.noop,
    )]
