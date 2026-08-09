"""Give every existing location the id path descendant lookups rely on.

Locations were flat before this, so each one becomes its own root. The path is
what makes "everything under this greenhouse" a prefix match instead of a
recursive walk, and a blank path would match the whole catalog, so no row may
be left without one.
"""

from django.db import migrations


def backfill_location_paths(apps, schema_editor):  # pylint: disable=unused-argument
    """Set each location's path from its own id."""
    Location = apps.get_model('locations', 'Location')
    for pk in Location.objects.values_list('pk', flat=True):
        Location.objects.filter(pk=pk).update(path=f'/{pk}/')


def clear_location_paths(apps, schema_editor):  # pylint: disable=unused-argument
    """Return every location to a blank path."""
    Location = apps.get_model('locations', 'Location')
    Location.objects.update(path='')


class Migration(migrations.Migration):
    """Populate paths for the locations that predate the hierarchy."""

    dependencies = [
        ("locations", "0003_hierarchy_and_capacity"),
    ]

    operations = [
        migrations.RunPython(backfill_location_paths, clear_location_paths),
    ]
