"""Seed conservative rules backed by facts already present in the nursery."""

from datetime import time

from django.db import migrations


DEFAULTS = (
    ('germination-check', 'Germination checks', 'germination_check', 'sowing_germination'),
    ('planned-milestone', 'Production milestones', 'stage_review', 'plan_milestone'),
    ('stage-review', 'Stage reviews', 'stage_review', 'stage_age'),
    ('ready-review', 'Ready-date reviews', 'ready_review', 'expected_ready'),
    ('maturity-review', 'Maturity and harvest reviews', 'harvest_review', 'sowing_maturity'),
)


def seed_rules(apps, _schema_editor):
    """Create only rules whose anchors have authoritative source dates."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='nursery'):
        for code, name, task_type, trigger in DEFAULTS:
            WorkTaskRule.objects.get_or_create(
                workspace=workspace,
                code=code,
                defaults={
                    'name': name,
                    'task_type': task_type,
                    'trigger': trigger,
                    'local_due_time': time(9),
                },
            )


class Migration(migrations.Migration):
    dependencies = [('work', '0001_initial')]
    operations = [migrations.RunPython(seed_rules, migrations.RunPython.noop)]
