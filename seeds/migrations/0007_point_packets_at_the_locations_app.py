"""Point a packet's storage location at the locations app.

State-only: the column and its constraint already reference the same table.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Record that a packet's container is a locations.Location."""

    dependencies = [
        ("seeds", "0006_map_legacy_packets_to_inventory"),
        ("locations", "0001_adopt_inventory_location"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="seedpacket",
                    name="storage_location",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="seed_packet",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="seedpacketreceiptdraft",
                    name="storage_location",
                    field=models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="seed_packet_draft",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
