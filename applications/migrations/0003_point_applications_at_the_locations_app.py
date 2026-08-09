"""Point an application's source location at the locations app.

State-only: the column and its constraint already reference the same table.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Record that an application draws stock from a locations.Location."""

    dependencies = [
        ("applications", "0002_inputapplicationtarget_seed_tray_generation_and_more"),
        ("locations", "0001_adopt_inventory_location"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="inputapplication",
                    name="source_location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="input_applications",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
