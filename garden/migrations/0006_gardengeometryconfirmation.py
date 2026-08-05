"""Record what an area's grid integers physically measure.

There is deliberately no data migration. Every existing area's `size_x` and
`size_y` are bare integers whose unit was never recorded, and the deployed
values could as easily be millimetres as metres or feet. Backfilling a guess
would silently license area-based calculations that are wrong by three orders
of magnitude, so existing areas start unconfirmed and an operator states the
scale for each one.
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
        ("workspaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GardenGeometryConfirmation",
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
                    "length_unit",
                    models.CharField(
                        choices=[
                            ("mm", "Millimetres"),
                            ("cm", "Centimetres"),
                            ("m", "Metres"),
                            ("in", "Inches"),
                            ("ft", "Feet"),
                        ],
                        max_length=8,
                    ),
                ),
                (
                    "cell_length",
                    models.DecimalField(
                        decimal_places=6,
                        help_text="Physical length of one grid step, in the chosen unit.",
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal("0.000001")
                            )
                        ],
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "confirmed_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "area",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="geometry_confirmations",
                        to="garden.gardenarea",
                    ),
                ),
                (
                    "confirmed_by",
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
                "ordering": ["-confirmed_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["area", "-confirmed_at"],
                        name="garden_geometry_latest_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("cell_length__gt", 0)),
                        name="garden_geometry_positive_cell_length",
                    )
                ],
            },
        ),
    ]
