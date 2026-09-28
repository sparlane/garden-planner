"""Give every cost layer, and every run, the day it happened.

Existing rows are backfilled from `created`, which is the only date they have.
That is the honest default and not a correction: a layer posted by the run
that reacted to the fact keeps saying exactly what it said before, so no filed
year moves. Where the two really did differ — an input applied in March and
posted in April — the backfill inherits the April reading the capture already
used, and only a re-costing of that batch will date it properly. Task 163
records it.

`cost_allocation_cohort_idx` goes at the same time: the new composite index
opens with the same column, so PostgreSQL answers a lookup by block alone from
it and the old one was a second copy of its own prefix.
"""

from django.db import migrations, models
from django.db.models import F


def stamp_with_the_run(apps, _schema_editor):
    """Date every existing layer, and every existing run, by when it was written."""
    apps.get_model('costing', 'CostAllocation').objects.update(effective_at=F('created'))
    apps.get_model('costing', 'CostAllocationRun').objects.update(occurred_at=F('created'))


def clear_the_columns(apps, _schema_editor):
    """Undo the backfill, so the fields can be dropped again."""
    apps.get_model('costing', 'CostAllocation').objects.update(effective_at=None)
    apps.get_model('costing', 'CostAllocationRun').objects.update(occurred_at=None)


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
        migrations.AddField(
            model_name="costallocationrun",
            name="occurred_at",
            field=models.DateTimeField(editable=False, null=True),
        ),
        migrations.RunPython(stamp_with_the_run, clear_the_columns),
        migrations.AlterField(
            model_name="costallocation",
            name="effective_at",
            field=models.DateTimeField(editable=False),
        ),
        migrations.AlterField(
            model_name="costallocationrun",
            name="occurred_at",
            field=models.DateTimeField(editable=False),
        ),
        migrations.RemoveIndex(
            model_name="costallocation",
            name="cost_allocation_cohort_idx",
        ),
        migrations.AddIndex(
            model_name="costallocation",
            index=models.Index(
                fields=["plant_cohort", "effective_at"],
                name="cost_allocation_cohort_at_idx",
            ),
        ),
    ]
