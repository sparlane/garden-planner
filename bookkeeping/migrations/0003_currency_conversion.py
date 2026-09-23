"""Give every taxable transaction somewhere to carry its exchange rate.

Nothing existing is altered and no amount is rewritten: the table is new, and
the rows already recorded keep every figure they carry in the currency they
were entered in. The backfill that follows this migration records the identity
conversion -- a rate of one, method `base_currency` -- for every settled row
already in its workspace's own currency, so that a row *without* a conversion
means one thing: a foreign amount whose rate nobody has typed yet. Those rows
are left alone here and are reported as a data-quality finding rather than
converted at a guess.
"""


import django.db.models.deletion
import workspaces.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookkeeping", "0002_taxretentionrecord_legalholdevent_and_more"),
        ("workspaces", "0010_workspace_conversion_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CurrencyConversion",
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
                ("source_type", models.CharField(max_length=64)),
                ("source_id", models.CharField(max_length=128)),
                ("source_currency_code", models.CharField(max_length=3)),
                (
                    "target_currency_code",
                    models.CharField(
                        help_text="The workspace currency as it stood when the rate was typed.",
                        max_length=3,
                    ),
                ),
                ("rate", models.DecimalField(decimal_places=10, max_digits=18)),
                (
                    "quote_direction",
                    models.CharField(
                        choices=[
                            (
                                "target_per_source",
                                "Workspace currency per one unit of the transaction currency",
                            ),
                            (
                                "source_per_target",
                                "Transaction currency per one unit of the workspace currency",
                            ),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "method",
                    models.CharField(
                        choices=[
                            ("spot", "Spot rate on the transaction date"),
                            ("period_end", "Rate at the end of the period"),
                            ("published", "Configured published rate"),
                            ("base_currency", "Already in the workspace currency"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "rate_source",
                    models.CharField(
                        help_text="Where the rate was taken from, in the words of whoever took it.",
                        max_length=255,
                    ),
                ),
                (
                    "effective_date",
                    models.DateField(help_text="The date the rate applied on."),
                ),
                (
                    "converted_on",
                    models.DateField(help_text="The date the conversion was made."),
                ),
                (
                    "amounts",
                    models.JSONField(
                        default=list,
                        help_text="One entry per converted amount: its name on the source row, what the row carries, and what that comes to at this rate.",
                    ),
                ),
                ("reason", models.TextField(blank=True, default="")),
                ("created", models.DateTimeField(auto_now_add=True)),
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
                    "supersedes",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="superseded_by",
                        to="bookkeeping.currencyconversion",
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
                "ordering": ["source_type", "source_id", "pk"],
                "indexes": [
                    models.Index(
                        fields=["workspace", "source_type", "source_id"],
                        name="bookkeeping_conversion_idx",
                    )
                ],
            },
        ),
    ]
