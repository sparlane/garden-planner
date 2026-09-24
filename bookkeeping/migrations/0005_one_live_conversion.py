"""Refuse a second current exchange rate for one transaction.

What happens to existing rows: nothing, unless a workspace already holds two
conversions for one record that supersede nothing -- which only two requests
arriving together could have produced. The constraint is added rather than the
rows rewritten, because choosing which of two recorded rates to keep is not a
migration's decision to make; such a workspace has to supersede one by hand,
and the migration fails loudly rather than picking.

With the one-to-one on `supersedes` already refusing to replace one conversion
twice, a single chain root per record means a single live rate per record.
"""


from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookkeeping", "0004_identity_conversions"),
        ("workspaces", "0010_workspace_conversion_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="currencyconversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("supersedes__isnull", True)),
                fields=("workspace", "source_type", "source_id"),
                name="bookkeeping_conversion_live_unique",
            ),
        ),
    ]
