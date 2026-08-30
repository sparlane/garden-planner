"""Add the germination-assessment task and seed its rule.

The germination check asks what has come up. This asks the question that
follows it: the window has passed, so is this sowing finished? Nothing could
answer that before `plantings.SowingGerminationClosure` existed, which is why
the rule is seeded now rather than with the others in 0002.
"""

from datetime import time

from django.db import migrations, models


def seed_rule(apps, _schema_editor):
    """Create the assessment rule for every nursery workspace."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='nursery'):
        WorkTaskRule.objects.get_or_create(
            workspace=workspace,
            code='germination-assessment',
            defaults={
                'name': 'Germination assessments',
                'task_type': 'germination_assessment',
                'trigger': 'sowing_germination_end',
                'local_due_time': time(9),
            },
        )


def remove_rule(apps, _schema_editor):
    """Remove only the rule this migration seeded."""
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    WorkTaskRule.objects.filter(code='germination-assessment').delete()


TASK_TYPES = [
    ('germination_check', 'Germination check'),
    ('germination_assessment', 'Germination assessment'),
    ('watering', 'Watering'),
    ('feeding', 'Feeding'),
    ('thinning', 'Thinning'),
    ('spacing', 'Spacing'),
    ('potting_on', 'Potting on'),
    ('hardening', 'Hardening'),
    ('ready_review', 'Ready-date review'),
    ('harvest_review', 'Harvest review'),
    ('stocktake', 'Stocktake'),
    ('order_picking', 'Order picking'),
    ('stage_review', 'Stage review'),
    ('health_inspection', 'Health inspection'),
    ('treatment_follow_up', 'Treatment follow-up'),
    ('reservation_review', 'Reservation review'),
    ('custom', 'Custom'),
]

TRIGGERS = [
    ('sowing_germination', 'Expected sowing germination'),
    ('sowing_germination_end', 'End of sowing germination window'),
    ('plan_milestone', 'Approved plan milestone'),
    ('stage_age', 'Current stage target age'),
    ('expected_ready', 'Recorded expected-ready date'),
    ('sowing_maturity', 'Expected sowing maturity'),
    ('calendar', 'Recurring calendar work'),
    ('health_follow_up', 'Health follow-up due'),
    ('reservation_expiry', 'Sales reservation expiry'),
]


class Migration(migrations.Migration):

    dependencies = [('work', '0006_seed_reservation_expiry_rule')]

    operations = [
        migrations.AlterField(
            model_name='worktask',
            name='task_type',
            field=models.CharField(choices=TASK_TYPES, max_length=32),
        ),
        migrations.AlterField(
            model_name='worktaskrule',
            name='task_type',
            field=models.CharField(choices=TASK_TYPES, max_length=32),
        ),
        migrations.AlterField(
            model_name='worktaskrule',
            name='trigger',
            field=models.CharField(choices=TRIGGERS, max_length=32),
        ),
        migrations.RunPython(seed_rule, remove_rule),
    ]
