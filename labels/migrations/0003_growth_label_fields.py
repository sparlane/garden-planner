from django.db import migrations


FIELDS = [
    'display',
    'variety',
    'batch',
    'stage',
    'grade',
    'container',
    'container_count',
    'expected_ready',
    'code',
    'print_date',
]


def add_growth_fields(apps, schema_editor):
    """Bring existing built-in templates in line with newly created ones."""
    Template = apps.get_model('labels', 'LabelTemplate')
    Template.objects.filter(built_in=True).update(fields=FIELDS)


class Migration(migrations.Migration):

    dependencies = [
        ('labels', '0002_backfill_labels_and_templates'),
        ('plantings', '0032_nurseryobservation_photo_url'),
    ]

    operations = [
        migrations.RunPython(add_growth_fields, migrations.RunPython.noop),
    ]
