"""Seed the reservation-expiry projection rule for existing Nurseries."""

from django.db import migrations


def seed_rule(apps, _schema_editor):
    """Create the rule only where Nursery sales workflows are available.

    The window starts two days before a hold lapses, because the point of the
    task is the days in which extending or fulfilling the hold is still a
    choice; the expiry itself is swept automatically.
    """
    Workspace = apps.get_model('workspaces', 'Workspace')
    WorkTaskRule = apps.get_model('work', 'WorkTaskRule')
    for workspace in Workspace.objects.filter(mode='nursery'):
        WorkTaskRule.objects.get_or_create(
            workspace=workspace,
            code='reservation-expiry',
            defaults={
                'name': 'Sales reservations lapsing',
                'task_type': 'reservation_review',
                'trigger': 'reservation_expiry',
                'due_start_offset_days': -2,
            },
        )


class Migration(migrations.Migration):
    dependencies = [('work', '0005_alter_worktask_task_type_and_more')]
    operations = [migrations.RunPython(seed_rule, migrations.RunPython.noop)]
