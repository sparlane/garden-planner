"""Add the planning-assumption review task and seed its rule.

The plan milestone asks whether a crop is on schedule. This asks the
question underneath it: the assumption that scheduled the crop has been
measured against what the crop did, and they no longer agree. Nothing
could raise that before the variance comparison existed, which is why the
rule is seeded here rather than with the others in 0002.
"""

from datetime import time

from django.db import migrations, models


def seed_rule(apps, _schema_editor):
    """Create the assumption review rule for every nursery workspace."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='nursery'):
        WorkTaskRule.objects.get_or_create(
            workspace=workspace,
            code='assumption-review',
            defaults={
                'name': 'Planning assumption reviews',
                'task_type': 'assumption_review',
                'trigger': 'assumption_variance',
                'local_due_time': time(9),
            },
        )


def remove_rule(apps, _schema_editor):
    """Remove only the rule this migration seeded."""
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    WorkTaskRule.objects.filter(code='assumption-review').delete()


class Migration(migrations.Migration):

    dependencies = [
        ("work", "0008_seed_garden_work_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="worktask",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("germination_check", "Germination check"),
                    ("germination_assessment", "Germination assessment"),
                    ("watering", "Watering"),
                    ("feeding", "Feeding"),
                    ("thinning", "Thinning"),
                    ("spacing", "Spacing"),
                    ("potting_on", "Potting on"),
                    ("hardening", "Hardening"),
                    ("ready_review", "Ready-date review"),
                    ("harvest_review", "Harvest review"),
                    ("stocktake", "Stocktake"),
                    ("order_picking", "Order picking"),
                    ("stage_review", "Stage review"),
                    ("health_inspection", "Health inspection"),
                    ("treatment_follow_up", "Treatment follow-up"),
                    ("reservation_review", "Reservation review"),
                    ("assumption_review", "Planning assumption review"),
                    ("custom", "Custom"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="worktaskrule",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("germination_check", "Germination check"),
                    ("germination_assessment", "Germination assessment"),
                    ("watering", "Watering"),
                    ("feeding", "Feeding"),
                    ("thinning", "Thinning"),
                    ("spacing", "Spacing"),
                    ("potting_on", "Potting on"),
                    ("hardening", "Hardening"),
                    ("ready_review", "Ready-date review"),
                    ("harvest_review", "Harvest review"),
                    ("stocktake", "Stocktake"),
                    ("order_picking", "Order picking"),
                    ("stage_review", "Stage review"),
                    ("health_inspection", "Health inspection"),
                    ("treatment_follow_up", "Treatment follow-up"),
                    ("reservation_review", "Reservation review"),
                    ("assumption_review", "Planning assumption review"),
                    ("custom", "Custom"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="worktaskrule",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("sowing_germination", "Expected sowing germination"),
                    ("sowing_germination_end", "End of sowing germination window"),
                    ("plan_milestone", "Approved plan milestone"),
                    ("stage_age", "Current stage target age"),
                    ("expected_ready", "Recorded expected-ready date"),
                    ("sowing_maturity", "Expected sowing maturity"),
                    ("calendar", "Recurring calendar work"),
                    ("health_follow_up", "Health follow-up due"),
                    ("reservation_expiry", "Sales reservation expiry"),
                    ("assumption_variance", "Diverged planning assumption"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(seed_rule, remove_rule),
    ]
