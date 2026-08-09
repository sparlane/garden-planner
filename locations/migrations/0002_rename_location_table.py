"""Rename the location table to match the app that now owns it.

Deliberately separate from `0001`: that migration moved ownership without
touching the database, so if this rename has to be rolled back on a live
deployment the model move survives it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Move the table from inventory_inventorylocation to locations_location."""

    dependencies = [
        ("locations", "0001_adopt_inventory_location"),
        ("inventory", "0007_point_stock_at_the_locations_app"),
    ]

    operations = [
        # Passing no table name restores Django's default, which is exactly
        # what the model declares, so `makemigrations --check` stays quiet.
        migrations.AlterModelTable(name="location", table=None),
    ]
