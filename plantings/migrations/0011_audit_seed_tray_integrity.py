from django.db import migrations
from django.db.models import F, Q


def describe_rows(queryset):
    """Return a bounded description of rows that failed the audit."""
    count = queryset.count()
    row_ids = list(queryset.order_by('pk').values_list('pk', flat=True)[:20])
    suffix = '' if count <= len(row_ids) else f' (first 20 of {count})'
    return f'{row_ids}{suffix}'


def audit_seed_tray_integrity(apps, _schema_editor):
    """Stop deployment when existing rows violate the new API invariants."""
    cell_planting_model = apps.get_model('plantings', 'SeedTrayCellPlanting')
    cell_model = apps.get_model('seedtrays', 'SeedTrayCell')

    invalid_memberships = cell_planting_model.objects.filter(
        Q(seed_tray_planting__seed_tray__isnull=True) |
        ~Q(cell__tray_id=F('seed_tray_planting__seed_tray_id'))
    )
    invalid_cells = cell_model.objects.filter(
        Q(x_position__lt=0) |
        Q(y_position__lt=0) |
        Q(x_position__gte=F('tray__model__x_cells')) |
        Q(y_position__gte=F('tray__model__y_cells'))
    )

    problems = []
    if invalid_memberships.exists():
        problems.append(
            'cross-tray SeedTrayCellPlanting IDs: '
            f'{describe_rows(invalid_memberships)}'
        )
    if invalid_cells.exists():
        problems.append(
            'out-of-bounds SeedTrayCell IDs: '
            f'{describe_rows(invalid_cells)}'
        )

    if problems:
        raise RuntimeError(
            'Seed-tray integrity audit failed. Repair these rows before retrying '
            f'the migration: {"; ".join(problems)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0010_datetimefield_germinated_started_ended'),
        ('seedtrays', '0002_datetimefield_created'),
    ]

    operations = [
        migrations.RunPython(audit_seed_tray_integrity, migrations.RunPython.noop),
    ]
