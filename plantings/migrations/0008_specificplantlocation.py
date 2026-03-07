import datetime

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("garden", "0002_gardenrow_gardensquare"),
        ("plantings", "0007_specificplant"),
        ("seedtrays", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecificPlantLocation",
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
                (
                    "location_type",
                    models.CharField(
                        choices=[
                            ("seed_tray_cell", "Seed Tray Cell"),
                            ("garden_square", "Garden Square"),
                        ],
                        max_length=20,
                    ),
                ),
                ("started", models.DateField(default=datetime.date.today)),
                ("ended", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "specific_plant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="locations",
                        to="plantings.specificplant",
                    ),
                ),
                (
                    "seed_tray_cell",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="seedtrays.seedtraycell",
                    ),
                ),
                (
                    "garden_square",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="garden.gardensquare",
                    ),
                ),
            ],
        ),
    ]
