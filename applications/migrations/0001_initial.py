"""Add the input application document, its lines, and their targets.

There is deliberately no data migration. A planting's quantity, a tray's cell
count, and a note about spraying all record that something happened, but none
of them identifies how much of which lot was used. Synthesising applications
from them would fabricate the exact consumption this document exists to prove,
so history starts empty and applications are recorded from the day this ships.
"""

import django.core.validators
import django.db.models.deletion
import workspaces.models
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("garden", "0006_gardengeometryconfirmation"),
        ("inventory", "0005_normalize_cell_volume_usage"),
        ("plantings", "0024_harvest"),
        ("seedtrays", "0004_seedtray_inventory_unit_seedtraymodel_inventory_item"),
        ("workspaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InputApplication",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("posted", "Posted"),
                            ("reversed", "Reversed"),
                        ],
                        default="draft",
                        editable=False,
                        max_length=16,
                    ),
                ),
                ("applied_at", models.DateTimeField()),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "target_summary",
                    models.TextField(blank=True, default="", editable=False),
                ),
                ("revision", models.PositiveIntegerField(default=0, editable=False)),
                (
                    "posted_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
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
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="input_applications",
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
                    "source_location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="input_applications",
                        to="inventory.inventorylocation",
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
                "ordering": ["-applied_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="InputApplicationLine",
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
                    "usage_basis",
                    models.CharField(
                        choices=[
                            ("cell_volume", "Cell volume"),
                            ("surface_area", "Surface-area rate"),
                            ("per_unit", "Per plant or item"),
                            ("fixed", "Fixed quantity"),
                            ("manual", "Manual"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "base_unit",
                    models.CharField(
                        choices=[
                            ("each", "Each"),
                            ("seed", "Seed"),
                            ("seed_cluster", "Seed cluster"),
                            ("ml", "Millilitre"),
                            ("l", "Litre"),
                            ("g", "Gram"),
                            ("kg", "Kilogram"),
                            ("m2", "Square metre"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "configured_rate",
                    models.DecimalField(
                        blank=True,
                        decimal_places=9,
                        max_digits=24,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("1E-9"))
                        ],
                    ),
                ),
                (
                    "configured_rate_unit",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("each", "Each"),
                            ("seed", "Seed"),
                            ("seed_cluster", "Seed cluster"),
                            ("ml", "Millilitre"),
                            ("l", "Litre"),
                            ("g", "Gram"),
                            ("kg", "Kilogram"),
                            ("m2", "Square metre"),
                        ],
                        default="",
                        max_length=16,
                    ),
                ),
                (
                    "configured_fixed_quantity",
                    models.DecimalField(
                        blank=True,
                        decimal_places=9,
                        max_digits=24,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("1E-9"))
                        ],
                    ),
                ),
                (
                    "fill_factor",
                    models.DecimalField(
                        blank=True,
                        decimal_places=6,
                        max_digits=12,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal("0.000001")
                            )
                        ],
                    ),
                ),
                (
                    "formula_basis_quantity",
                    models.DecimalField(
                        blank=True, decimal_places=9, max_digits=24, null=True
                    ),
                ),
                (
                    "formula_basis_unit",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("each", "Each"),
                            ("seed", "Seed"),
                            ("seed_cluster", "Seed cluster"),
                            ("ml", "Millilitre"),
                            ("l", "Litre"),
                            ("g", "Gram"),
                            ("kg", "Kilogram"),
                            ("m2", "Square metre"),
                        ],
                        default="",
                        max_length=16,
                    ),
                ),
                (
                    "calculated_base_quantity",
                    models.DecimalField(
                        blank=True, decimal_places=9, max_digits=24, null=True
                    ),
                ),
                (
                    "applied_quantity",
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
                        blank=True,
                        choices=[
                            ("each", "Each"),
                            ("seed", "Seed"),
                            ("seed_cluster", "Seed cluster"),
                            ("ml", "Millilitre"),
                            ("l", "Litre"),
                            ("g", "Gram"),
                            ("kg", "Kilogram"),
                            ("m2", "Square metre"),
                        ],
                        max_length=16,
                        null=True,
                    ),
                ),
                (
                    "applied_base_quantity",
                    models.DecimalField(
                        decimal_places=9,
                        max_digits=24,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("1E-9"))
                        ],
                    ),
                ),
                (
                    "waste_quantity",
                    models.DecimalField(
                        decimal_places=9,
                        default=Decimal("0"),
                        max_digits=24,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "waste_base_quantity",
                    models.DecimalField(
                        decimal_places=9,
                        default=Decimal("0"),
                        max_digits=24,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                ("waste_reason", models.TextField(blank=True, default="")),
                ("override_reason", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="applications.inputapplication",
                    ),
                ),
                (
                    "consumption_movement",
                    models.OneToOneField(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_consumption",
                        to="inventory.stockmovement",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_lines",
                        to="inventory.inventoryitem",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_lines",
                        to="inventory.stocklot",
                    ),
                ),
                (
                    "unit_conversion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_lines",
                        to="inventory.itemunitconversion",
                    ),
                ),
                (
                    "waste_movement",
                    models.OneToOneField(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_waste",
                        to="inventory.stockmovement",
                    ),
                ),
            ],
            options={
                "ordering": ["pk"],
            },
        ),
        migrations.CreateModel(
            name="InputApplicationTarget",
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
                    "target_type",
                    models.CharField(
                        choices=[
                            ("batch", "Production batch"),
                            ("seed_tray_cell", "Tray cell"),
                            ("specific_plant", "Plant"),
                            ("inventory_unit", "Serialized unit"),
                            ("garden_area", "Garden area"),
                            ("garden_bed", "Garden bed"),
                            ("garden_row", "Garden row"),
                            ("garden_square", "Garden square"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "weight",
                    models.DecimalField(
                        decimal_places=6,
                        default=Decimal("1"),
                        help_text="Share of this target that received the input.",
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal("0.000001")
                            )
                        ],
                    ),
                ),
                ("cell_volume_ml", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "area_m2",
                    models.DecimalField(
                        blank=True, decimal_places=6, max_digits=18, null=True
                    ),
                ),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="plantings.productionbatch",
                    ),
                ),
                (
                    "garden_area",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="garden.gardenarea",
                    ),
                ),
                (
                    "garden_bed",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="garden.gardenbed",
                    ),
                ),
                (
                    "garden_row",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="garden.gardenrow",
                    ),
                ),
                (
                    "garden_square",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="garden.gardensquare",
                    ),
                ),
                (
                    "inventory_unit",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="inventory.inventoryunit",
                    ),
                ),
                (
                    "line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="targets",
                        to="applications.inputapplicationline",
                    ),
                ),
                (
                    "seed_tray_cell",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="seedtrays.seedtraycell",
                    ),
                ),
                (
                    "specific_plant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="application_targets",
                        to="plantings.specificplant",
                    ),
                ),
            ],
            options={
                "ordering": ["pk"],
            },
        ),
        migrations.AddIndex(
            model_name="inputapplication",
            index=models.Index(
                fields=["workspace", "applied_at"], name="application_period_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="inputapplication",
            index=models.Index(
                fields=["batch", "applied_at"], name="application_batch_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplication",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("posted_at__isnull", True),
                        ("reversed_at__isnull", True),
                        ("status", "draft"),
                    ),
                    models.Q(
                        ("posted_at__isnull", False),
                        ("reversed_at__isnull", True),
                        ("status", "posted"),
                    ),
                    models.Q(
                        ("posted_at__isnull", False),
                        ("reversed_at__isnull", False),
                        ("status", "reversed"),
                    ),
                    _connector="OR",
                ),
                name="application_status_stamp",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationline",
            constraint=models.CheckConstraint(
                condition=models.Q(("applied_base_quantity__gt", 0)),
                name="application_line_positive_applied",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationline",
            constraint=models.CheckConstraint(
                condition=models.Q(("waste_base_quantity__gte", 0)),
                name="application_line_nonnegative_waste",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("fill_factor__isnull", True),
                    ("fill_factor__gt", 0),
                    _connector="OR",
                ),
                name="application_line_positive_fill_factor",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("configured_rate__isnull", True),
                    ("configured_rate__gt", 0),
                    _connector="OR",
                ),
                name="application_line_positive_rate",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationline",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("calculated_base_quantity__isnull", True),
                    ("calculated_base_quantity__gte", 0),
                    _connector="OR",
                ),
                name="application_line_nonnegative_calculated",
            ),
        ),
        migrations.AddIndex(
            model_name="inputapplicationtarget",
            index=models.Index(
                fields=["line", "target_type"], name="application_target_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("batch__isnull", False),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "batch"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", False),
                        ("specific_plant__isnull", True),
                        ("target_type", "seed_tray_cell"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", False),
                        ("target_type", "specific_plant"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", False),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "inventory_unit"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", False),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "garden_area"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", False),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "garden_bed"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", False),
                        ("garden_square__isnull", True),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "garden_row"),
                    ),
                    models.Q(
                        ("batch__isnull", True),
                        ("garden_area__isnull", True),
                        ("garden_bed__isnull", True),
                        ("garden_row__isnull", True),
                        ("garden_square__isnull", False),
                        ("inventory_unit__isnull", True),
                        ("seed_tray_cell__isnull", True),
                        ("specific_plant__isnull", True),
                        ("target_type", "garden_square"),
                    ),
                    _connector="OR",
                ),
                name="application_target_identity",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.CheckConstraint(
                condition=models.Q(("weight__gt", 0)),
                name="application_target_positive_weight",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("cell_volume_ml__isnull", True),
                    ("cell_volume_ml__gt", 0),
                    _connector="OR",
                ),
                name="application_target_positive_cell_volume",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("area_m2__isnull", True), ("area_m2__gt", 0), _connector="OR"
                ),
                name="application_target_positive_area",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("batch__isnull", False)),
                fields=("line", "batch"),
                name="application_target_unique_batch",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("seed_tray_cell__isnull", False)),
                fields=("line", "seed_tray_cell"),
                name="application_target_unique_seed_tray_cell",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("specific_plant__isnull", False)),
                fields=("line", "specific_plant"),
                name="application_target_unique_specific_plant",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("inventory_unit__isnull", False)),
                fields=("line", "inventory_unit"),
                name="application_target_unique_inventory_unit",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("garden_area__isnull", False)),
                fields=("line", "garden_area"),
                name="application_target_unique_garden_area",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("garden_bed__isnull", False)),
                fields=("line", "garden_bed"),
                name="application_target_unique_garden_bed",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("garden_row__isnull", False)),
                fields=("line", "garden_row"),
                name="application_target_unique_garden_row",
            ),
        ),
        migrations.AddConstraint(
            model_name="inputapplicationtarget",
            constraint=models.UniqueConstraint(
                condition=models.Q(("garden_square__isnull", False)),
                fields=("line", "garden_square"),
                name="application_target_unique_garden_square",
            ),
        ),
    ]
