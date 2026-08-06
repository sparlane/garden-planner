"""Configure when an input application override needs explaining.

Existing workspaces take a five per cent tolerance and no floor, which asks for
a reason whenever a confirmed quantity differs materially from the calculated
suggestion and never for a rounding-sized drift on a large line. Raising the
floor suppresses the small-line case too.
"""

import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="override_tolerance_floor",
            field=models.DecimalField(
                decimal_places=9,
                default=Decimal("0"),
                help_text="Smallest difference in an item base unit that can require a reason, so a rounding-sized drift never does. Zero disables it.",
                max_digits=24,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="override_tolerance_percent",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("5"),
                help_text="How far a confirmed input quantity may differ from the calculated suggestion, as a percentage, before a reason is required.",
                max_digits=7,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("override_tolerance_percent__gte", 0),
                    ("override_tolerance_percent__lte", 100),
                ),
                name="workspace_override_percent_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.CheckConstraint(
                condition=models.Q(("override_tolerance_floor__gte", 0)),
                name="workspace_override_floor_nonnegative",
            ),
        ),
    ]
