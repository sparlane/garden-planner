from django.db import migrations, models
from django.db.models import F, Q


def find_location_overlaps(locations):
    """Return bounded overlap pairs and the total overlap count."""
    overlap_pairs = []
    overlap_count = 0
    current_plant_id = None
    open_interval = None

    for location in locations:
        if location['specific_plant_id'] != current_plant_id:
            current_plant_id = location['specific_plant_id']
            open_interval = None

        started = location['started']
        ended = location['ended']
        if ended is not None and ended <= started:
            continue

        if open_interval is not None:
            open_end = open_interval['ended']
            if open_end is None or started < open_end:
                overlap_count += 1
                if len(overlap_pairs) < 20:
                    overlap_pairs.append((open_interval['pk'], location['pk']))
                if open_end is None:
                    continue
                if ended is None or ended > open_end:
                    open_interval = location
                continue

        open_interval = location

    return overlap_pairs, overlap_count


def audit_location_chronology(apps, _schema_editor):
    """Stop deployment when existing location history violates chronology."""
    location_model = apps.get_model('plantings', 'SpecificPlantLocation')
    reversed_locations = location_model.objects.filter(ended__lt=F('started'))
    reversed_count = reversed_locations.count()
    reversed_ids = list(
        reversed_locations.order_by('pk').values_list('pk', flat=True)[:20]
    )

    ordered_locations = (
        location_model.objects
        .exclude(ended__lt=F('started'))
        .order_by('specific_plant_id', 'started', 'pk')
        .values('pk', 'specific_plant_id', 'started', 'ended')
        .iterator()
    )
    overlap_pairs, overlap_count = find_location_overlaps(ordered_locations)

    problems = []
    if reversed_count:
        suffix = '' if reversed_count <= 20 else f' (first 20 of {reversed_count})'
        problems.append(f'reversed location IDs: {reversed_ids}{suffix}')
    if overlap_count:
        suffix = '' if overlap_count <= 20 else f' (first 20 of {overlap_count})'
        problems.append(f'overlapping location ID pairs: {overlap_pairs}{suffix}')

    if problems:
        raise RuntimeError(
            'Location chronology audit failed. Repair these rows before retrying '
            f'the migration: {"; ".join(problems)}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0011_audit_seed_tray_integrity'),
    ]

    operations = [
        migrations.RunPython(audit_location_chronology, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='specificplantlocation',
            constraint=models.CheckConstraint(
                condition=Q(ended__isnull=True) | Q(ended__gte=F('started')),
                name='location_ended_not_before_started',
            ),
        ),
    ]
