"""Seed health follow-up projection rules for existing Nurseries."""

from django.db import migrations


def seed_rule(apps, _schema_editor):
    """Create the rule only where Nursery health workflows are available."""
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='nursery'):
        WorkTaskRule.objects.get_or_create(
            workspace=workspace,
            code='health-follow-up',
            defaults={
                'name': 'Plant health follow-ups',
                'task_type': 'health_inspection',
                'trigger': 'health_follow_up',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('work', '0003_alter_worktask_task_type_and_more')]
    operations = [migrations.RunPython(seed_rule, migrations.RunPython.noop)]
