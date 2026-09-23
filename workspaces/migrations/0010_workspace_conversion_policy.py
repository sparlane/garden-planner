"""Name the one rate a workspace converts its foreign amounts at.

Every existing workspace takes the spot rate, which is the rate an operator
typing a rate against a transaction is most likely to be holding. Nothing is
inspected and no amount is converted: this only says which method a conversion
recorded from here on may claim, and a workspace that converts another way
changes the setting before recording one.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0009_workspace_multi_currency_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="conversion_policy",
            field=models.CharField(
                choices=[
                    ("spot", "Spot rate on the transaction date"),
                    ("period_end", "Rate at the end of the period"),
                    ("published", "Configured published rate"),
                ],
                default="spot",
                help_text="Which rate every foreign amount in this workspace is converted at.",
                max_length=16,
            ),
        ),
    ]
