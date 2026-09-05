"""Say how far a planning assumption may drift before it is questioned.

Existing workspaces take a ten per cent tolerance over at least five
batches, which is wide enough that a settled figure is left alone and
strict enough that an assumed 0.85 germination against an observed 0.6 is
raised the season it happens rather than the one after.
"""


import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0007_workspace_seller_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="assumption_minimum_samples",
            field=models.PositiveSmallIntegerField(
                default=5,
                help_text="Smallest number of batches behind an observed figure that can raise a flag, so three trays never look like evidence.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="assumption_tolerance_percent",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("10"),
                help_text="How far an observed planning figure may differ from the assumption that predicted it, as a percentage of the assumption, before the variance report flags it for revision.",
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
                    ("assumption_tolerance_percent__gte", 0),
                    ("assumption_tolerance_percent__lte", 100),
                ),
                name="workspace_assumption_percent_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.CheckConstraint(
                condition=models.Q(("assumption_minimum_samples__gte", 1)),
                name="workspace_assumption_samples_positive",
            ),
        ),
    ]
