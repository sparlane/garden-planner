"""Adopt the existing location table into the locations app.

The model moves out of `inventory` because stock is not the only thing that
stands somewhere: trays and individual plants use the same physical places.
Nothing about the table changes here, so the whole migration is state-only —
`db_table` still names the inventory table and the unique constraint keeps its
original name. `0002` renames the table once every app agrees who owns it.
"""

import django.db.models.deletion
import workspaces.models
from django.db import migrations, models


class Migration(migrations.Migration):
    """Take ownership of the location model without touching the database."""

    initial = True

    dependencies = [
        ("workspaces", "0002_workspace_override_tolerance_floor_and_more"),
        ("inventory", "0006_stockreceipt_price_includes_tax"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Location",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("name", models.CharField(max_length=255)),
                        ("code", models.CharField(max_length=64)),
                        (
                            "location_type",
                            models.CharField(
                                choices=[
                                    ("receiving", "Receiving"),
                                    ("storage", "Storage"),
                                    ("growing", "Nursery or growing area"),
                                    ("dispatch", "Customer dispatch"),
                                    ("quarantine", "Quarantine"),
                                    ("adjustment", "Adjustment"),
                                    ("seed_packet", "Seed packet"),
                                ],
                                max_length=16,
                            ),
                        ),
                        ("active", models.BooleanField(default=True)),
                        ("notes", models.TextField(blank=True, default="")),
                        ("created", models.DateTimeField(auto_now_add=True)),
                        ("updated", models.DateTimeField(auto_now=True)),
                        (
                            "workspace",
                            models.ForeignKey(
                                default=workspaces.models.get_default_workspace_id,
                                editable=False,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="workspaces.workspace",
                            ),
                        ),
                    ],
                    options={
                        "ordering": ["name", "pk"],
                        "db_table": "inventory_inventorylocation",
                    },
                ),
                migrations.AddConstraint(
                    model_name="location",
                    constraint=models.UniqueConstraint(
                        fields=("workspace", "code"),
                        name="inventory_location_workspace_code_unique",
                    ),
                ),
            ],
        ),
    ]
