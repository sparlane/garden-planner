"""Give the cohort losses already recorded an explicit unspecified cause.

Cohorts collapsed every way of losing stock into one `LOSS` action explained by
free text, while the plant events beside them distinguished failure, loss at
stocktake, and culling. The cause becomes structured here, which leaves the rows
already stored with no answer. Their reason text sometimes hints at one, but
reading `"gone"` as a stocktake loss is a guess this migration would be putting
into a report that people act on, so every existing loss is recorded as
`unspecified` and counted separately by the reports instead.
"""

from django.conf import settings
from django.db import migrations, models


LOSS = 'loss'
UNSPECIFIED = 'unspecified'


def record_unspecified_cause(apps, _schema_editor):
    """Mark every stored loss unspecified and say how many there were."""
    operation_model = apps.get_model('plantings', 'CohortOperation')
    stored = operation_model.objects.filter(action=LOSS).exclude(
        loss_cause=UNSPECIFIED,
    )
    count = stored.update(loss_cause=UNSPECIFIED)
    if count:
        print(
            f'  Recorded {count} existing cohort '
            f'{"loss" if count == 1 else "losses"} as unspecified: their cause '
            'predates the field and was not inferred from their reason text.'
        )


def clear_cause(apps, _schema_editor):
    """Return every operation to no recorded cause when rewinding."""
    operation_model = apps.get_model('plantings', 'CohortOperation')
    operation_model.objects.exclude(loss_cause='').update(loss_cause='')


class Migration(migrations.Migration):

    dependencies = [
        ("plantings", "0044_gardenplantingstatusevent"),
        ("workspaces", "0007_workspace_seller_identity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cohortoperation",
            name="loss_cause",
            field=models.CharField(
                blank=True,
                choices=[
                    ("failed", "Failed"),
                    ("lost", "Lost during stocktake"),
                    ("culled", "Culled"),
                    ("donated", "Donated"),
                    ("unspecified", "Unspecified"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.RunPython(record_unspecified_cause, clear_cause),
        migrations.AddConstraint(
            model_name="cohortoperation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("action", "loss"), models.Q(("loss_cause", ""), _negated=True)
                    ),
                    models.Q(
                        models.Q(("action", "loss"), _negated=True), ("loss_cause", "")
                    ),
                    _connector="OR",
                ),
                name="cohort_operation_loss_cause_matches_action",
            ),
        ),
    ]
