"""Add the harvest record and its attribution to individual plants.

There is deliberately no data migration. `removed=True` on a planting records
that it stopped being tracked and carries no quantity, unit, or date, so it
cannot say whether the crop was harvested, failed, or simply pulled out.
Inventing harvests from it would fabricate yield history. Every existing
planting already carries a production batch from the task 40 backfill, so
historical crops become harvestable by hand as soon as this ships.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import workspaces.models
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("garden", "0005_workspace_ownership"),
        ("plantings", "0023_backfill_plant_lifecycle_events"),
        ("workspaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Harvest",
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
                ("harvested_at", models.DateTimeField()),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=9,
                        max_digits=24,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("1E-9"))
                        ],
                    ),
                ),
                (
                    "unit_code",
                    models.CharField(
                        choices=[
                            ("each", "Each"),
                            ("g", "Gram"),
                            ("kg", "Kilogram"),
                            ("ml", "Millilitre"),
                            ("l", "Litre"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "quality_rating",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(5),
                        ],
                    ),
                ),
                (
                    "grade",
                    models.CharField(
                        choices=[
                            ("ungraded", "Ungraded"),
                            ("premium", "Premium"),
                            ("standard", "Standard"),
                            ("seconds", "Seconds"),
                        ],
                        default="ungraded",
                        max_length=16,
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[("posted", "Posted"), ("reversed", "Reversed")],
                        default="posted",
                        editable=False,
                        max_length=16,
                    ),
                ),
                (
                    "posted_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                (
                    "reversed_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                (
                    "reverse_reason",
                    models.TextField(blank=True, default="", editable=False),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="harvests",
                        to="plantings.productionbatch",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "garden_row",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="harvests",
                        to="garden.gardenrow",
                    ),
                ),
                (
                    "garden_square",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="harvests",
                        to="garden.gardensquare",
                    ),
                ),
                (
                    "reversed_by",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
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
                "ordering": ["-harvested_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="HarvestPlant",
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
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "harvest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="plant_allocations",
                        to="plantings.harvest",
                    ),
                ),
                (
                    "plant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="harvest_allocations",
                        to="plantings.specificplant",
                    ),
                ),
            ],
            options={
                "ordering": ["harvest", "plant"],
            },
        ),
        migrations.AddIndex(
            model_name="harvest",
            index=models.Index(
                fields=["batch", "harvested_at"], name="harvest_batch_period_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="harvest",
            index=models.Index(
                fields=["workspace", "harvested_at"], name="harvest_period_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="harvest",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantity__gt", 0)),
                name="harvest_quantity_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="harvest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("garden_square__isnull", True)),
                    models.Q(("garden_row__isnull", True)),
                    _connector="OR",
                ),
                name="harvest_single_location",
            ),
        ),
        migrations.AddConstraint(
            model_name="harvest",
            constraint=models.CheckConstraint(
                condition=models.Q(("unit_code__in", ["each", "g", "kg", "ml", "l"])),
                name="harvest_allowed_unit",
            ),
        ),
        migrations.AddConstraint(
            model_name="harvest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("reversed_at__isnull", True), ("status", "posted")),
                    models.Q(("reversed_at__isnull", False), ("status", "reversed")),
                    _connector="OR",
                ),
                name="harvest_reversal_stamp",
            ),
        ),
        migrations.AddConstraint(
            model_name="harvestplant",
            constraint=models.UniqueConstraint(
                fields=("harvest", "plant"), name="harvest_plant_unique"
            ),
        ),
    ]
