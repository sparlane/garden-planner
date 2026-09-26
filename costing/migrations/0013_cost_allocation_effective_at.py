"""Give every cost layer the day it happened, beside the day it was written.

Existing rows are backfilled from `created`, which is the only date they have.
That is the honest default and not a correction: a layer posted by the run that
reacted to the fact keeps saying exactly what it said before, so no filed year
moves. Where the two really did differ — an input applied in March and posted
in April — the backfill inherits the April reading the capture already used,
and only a re-costing of that batch will date it properly. Task 163 records it.
"""

from django.db import migrations, models
from django.db.models import F


def stamp_with_the_run(apps, _schema_editor):
    """Date every existing layer by the run that wrote it."""
    apps.get_model('costing', 'CostAllocation').objects.update(effective_at=F('created'))


def clear_the_column(apps, _schema_editor):
    """Undo the backfill, so the field can be dropped again."""
    apps.get_model('costing', 'CostAllocation').objects.update(effective_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("costing", "0012_remove_costallocation_cost_allocation_source_identity_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="costallocation",
            name="effective_at",
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.RunPython(stamp_with_the_run, clear_the_column),
        migrations.AlterField(
            model_name="costallocation",
            name="effective_at",
            field=models.DateTimeField(editable=False),
        ),
        migrations.AddIndex(
            model_name="costallocation",
            index=models.Index(
                fields=["plant_cohort", "effective_at"],
                name="cost_allocation_cohort_at_idx",
            ),
        ),
    ]
