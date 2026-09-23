"""Ask for a currency only where a workspace says it needs to be asked.

Every existing workspace takes the default and stops being asked, whatever
it has recorded: nothing is inspected and nothing is rewritten. A receipt
already in euros keeps its euros and still refuses to be totalled with
dollars; what changes is that no new record is asked the question until an
operator turns the switch on.
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0008_assumption_variance"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="multi_currency_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Whether a new record may be entered in a currency other than the workspace currency.",
            ),
        ),
    ]
