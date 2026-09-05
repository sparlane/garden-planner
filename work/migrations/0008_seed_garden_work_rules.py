"""Give existing Garden workspaces shared care projections."""

from datetime import time

from django.db import migrations


DEFAULTS = (
    ('germination-check', 'Germination checks', 'germination_check', 'sowing_germination'),
    ('germination-assessment', 'Germination assessments', 'germination_assessment', 'sowing_germination_end'),
    ('planned-milestone', 'Production milestones', 'stage_review', 'plan_milestone'),
    ('stage-review', 'Stage reviews', 'stage_review', 'stage_age'),
    ('ready-review', 'Ready-date reviews', 'ready_review', 'expected_ready'),
    ('maturity-review', 'Maturity and harvest reviews', 'harvest_review', 'sowing_maturity'),
    ('health-follow-up', 'Plant health follow-ups', 'health_inspection', 'health_follow_up'),
)


def seed_garden_rules(apps, _schema_editor):
    """Make due-work layers useful in existing private gardens."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='garden'):
        for code, name, task_type, trigger in DEFAULTS:
            WorkTaskRule.objects.get_or_create(
                workspace=workspace, code=code,
                defaults={
                    'name': name, 'task_type': task_type,
                    'trigger': trigger, 'local_due_time': time(9),
                },
            )


class Migration(migrations.Migration):
    dependencies = [('work', '0007_germination_assessment_rule')]
    operations = [migrations.RunPython(
        seed_garden_rules, migrations.RunPython.noop,
    )]
