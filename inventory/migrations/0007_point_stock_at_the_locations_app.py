"""Hand the location model over to the locations app.

Every foreign key here already points at the same table; only the app label
Django records for it changes. That makes the whole migration state-only, so no
constraint is dropped and rebuilt on a live database.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Repoint stock's location keys and give up ownership of the model."""

    dependencies = [
        ("inventory", "0006_stockreceipt_price_includes_tax"),
        ("locations", "0001_adopt_inventory_location"),
        ("seeds", "0007_point_packets_at_the_locations_app"),
        ("applications", "0003_point_applications_at_the_locations_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="stockreceiptline",
                    name="destination",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="receipt_lines",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="inventoryunit",
                    name="current_location",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="serialized_units",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="stocktakeline",
                    name="location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stocktake_lines",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="stockmovement",
                    name="source",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outgoing_stock_movements",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="stockmovement",
                    name="destination",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="incoming_stock_movements",
                        to="locations.location",
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name="inventorylocation",
                    name="inventory_location_workspace_code_unique",
                ),
                migrations.DeleteModel(name="InventoryLocation"),
            ],
        ),
    ]
