from django.db import migrations
from django.db.models import F, Sum


def describe_rows(queryset, count):
    """Return a bounded description of rows that failed the audit."""
    row_ids = list(queryset.order_by('pk').values_list('pk', flat=True)[:20])
    suffix = '' if count <= len(row_ids) else f' (first 20 of {count})'
    return f'{row_ids}{suffix}'


def audit_seed_allocation_capacity(apps, _schema_editor):
    """Stop deployment when cell seed totals exceed their parent sowing."""
    planting_model = apps.get_model('plantings', 'SeedTrayPlanting')

    over_allocated = planting_model.objects.annotate(
        allocated_quantity=Sum('cell_plantings__quantity', default=0),
    ).filter(allocated_quantity__gt=F('quantity'))

    problems = []
    over_allocated_count = over_allocated.count()
    if over_allocated_count:
        problems.append(
            'over-allocated SeedTrayPlanting IDs: '
            f'{describe_rows(over_allocated, over_allocated_count)}'
        )
    if problems:
        raise RuntimeError(
            'Seed allocation capacity audit failed. Repair these rows before '
            f'retrying the migration: {"; ".join(problems)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0014_constrain_positive_quantities'),
    ]

    operations = [
        migrations.RunPython(
            audit_seed_allocation_capacity,
            migrations.RunPython.noop,
        ),
    ]
