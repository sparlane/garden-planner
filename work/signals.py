"""Create conservative automation defaults for operational workspaces."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from workspaces.models import Workspace

from .models import WorkTaskRule, WorkTaskType


DEFAULT_RULES = (
    ('germination-check', 'Germination checks', WorkTaskType.GERMINATION, WorkTaskRule.Trigger.GERMINATION),
    ('germination-assessment', 'Germination assessments', WorkTaskType.GERMINATION_ASSESSMENT, WorkTaskRule.Trigger.GERMINATION_WINDOW_END),
    ('planned-milestone', 'Production milestones', WorkTaskType.STAGE, WorkTaskRule.Trigger.PLAN_MILESTONE),
    ('stage-review', 'Stage reviews', WorkTaskType.STAGE, WorkTaskRule.Trigger.STAGE_AGE),
    ('ready-review', 'Ready-date reviews', WorkTaskType.READY, WorkTaskRule.Trigger.EXPECTED_READY),
    ('maturity-review', 'Maturity and harvest reviews', WorkTaskType.HARVEST, WorkTaskRule.Trigger.MATURITY),
    ('health-follow-up', 'Plant health follow-ups', WorkTaskType.HEALTH_INSPECTION, WorkTaskRule.Trigger.HEALTH_FOLLOW_UP),
)


def ensure_default_rules(workspace):
    """Idempotently install only rules backed by authoritative source dates."""
    for code, name, task_type, trigger in DEFAULT_RULES:
        WorkTaskRule.objects.get_or_create(
            workspace=workspace, code=code,
            defaults={'name': name, 'task_type': task_type, 'trigger': trigger},
        )


@receiver(post_save, sender=Workspace)
def workspace_saved(sender, instance, **_kwargs):  # pylint: disable=unused-argument
    """Keep every garden or nursery supplied with useful work rules."""
    ensure_default_rules(instance)
