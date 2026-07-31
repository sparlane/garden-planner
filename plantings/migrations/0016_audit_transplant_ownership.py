from django.db import migrations


def describe_rows(queryset):
    """Return a bounded description of conflicting aggregate rows."""
    count = queryset.count()
    row_ids = list(queryset.order_by('pk').values_list('pk', flat=True)[:20])
    suffix = '' if count <= len(row_ids) else f' (first 20 of {count})'
    return f'{row_ids}{suffix}'


def audit_transplant_ownership(apps, _schema_editor):
    """Reject plantings represented by aggregate and individual transplants."""
    transplant_model = apps.get_model('plantings', 'GardenSquareTransplant')
    conflicting_transplants = transplant_model.objects.filter(
        original_planting__cell_plantings__specific_plants__locations__location_type=(
            'garden_square'
        ),
    ).distinct()

    if conflicting_transplants.exists():
        raise RuntimeError(
            'Transplant ownership audit failed. Repair aggregate '
            'GardenSquareTransplant rows that overlap individual garden '
            'locations before retrying the migration. Inspect each row with '
            '`python manage.py convert_legacy_transplant ID`, preview its '
            'conversion with the requested source and history arguments, then '
            'rerun with `--apply`. Conflicting '
            'GardenSquareTransplant IDs: '
            f'{describe_rows(conflicting_transplants)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0015_audit_seed_allocation_capacity'),
    ]

    operations = [
        migrations.RunPython(
            audit_transplant_ownership,
            migrations.RunPython.noop,
        ),
    ]
