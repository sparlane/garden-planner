import datetime

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plantings", "0006_seedtraycellplanting"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecificPlant",
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
                ("germinated", models.DateField(default=datetime.date.today)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "cell_planting",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="specific_plants",
                        to="plantings.seedtraycellplanting",
                    ),
                ),
            ],
        ),
    ]
