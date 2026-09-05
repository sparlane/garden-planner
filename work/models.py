"""Workspace-scoped nursery work rules, acknowledgements, and audit history."""

# These workspace catalogs intentionally follow the same ownership and audit
# conventions as the existing catalogs.
# pylint: disable=duplicate-code

from datetime import date

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from workspaces.models import WorkspaceOwnedModel


class WorkTaskType(models.TextChoices):
    """Stable categories shared by rules, projections, and manual work."""

    GERMINATION = 'germination_check', 'Germination check'
    GERMINATION_ASSESSMENT = 'germination_assessment', 'Germination assessment'
    WATERING = 'watering', 'Watering'
    FEEDING = 'feeding', 'Feeding'
    THINNING = 'thinning', 'Thinning'
    SPACING = 'spacing', 'Spacing'
    POTTING = 'potting_on', 'Potting on'
    HARDENING = 'hardening', 'Hardening'
    READY = 'ready_review', 'Ready-date review'
    HARVEST = 'harvest_review', 'Harvest review'
    STOCKTAKE = 'stocktake', 'Stocktake'
    ORDER_PICKING = 'order_picking', 'Order picking'
    STAGE = 'stage_review', 'Stage review'
    HEALTH_INSPECTION = 'health_inspection', 'Health inspection'
    TREATMENT_FOLLOW_UP = 'treatment_follow_up', 'Treatment follow-up'
    RESERVATION = 'reservation_review', 'Reservation review'
    ASSUMPTION = 'assumption_review', 'Planning assumption review'
    CUSTOM = 'custom', 'Custom'


class WorkTaskRule(WorkspaceOwnedModel):
    """Configuration that projects source facts into actionable work."""

    class Trigger(models.TextChoices):
        """Available authoritative scheduling anchors."""

        GERMINATION = 'sowing_germination', 'Expected sowing germination'
        GERMINATION_WINDOW_END = 'sowing_germination_end', 'End of sowing germination window'
        PLAN_MILESTONE = 'plan_milestone', 'Approved plan milestone'
        STAGE_AGE = 'stage_age', 'Current stage target age'
        EXPECTED_READY = 'expected_ready', 'Recorded expected-ready date'
        MATURITY = 'sowing_maturity', 'Expected sowing maturity'
        CALENDAR = 'calendar', 'Recurring calendar work'
        HEALTH_FOLLOW_UP = 'health_follow_up', 'Health follow-up due'
        RESERVATION_EXPIRY = 'reservation_expiry', 'Sales reservation expiry'
        ASSUMPTION_VARIANCE = 'assumption_variance', 'Diverged planning assumption'

    class Frequency(models.TextChoices):
        """Calendar recurrence units evaluated in workspace local time."""

        NONE = '', 'Does not repeat'
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    task_type = models.CharField(max_length=32, choices=WorkTaskType.choices)
    trigger = models.CharField(max_length=32, choices=Trigger.choices)
    active = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=20)
    due_start_offset_days = models.IntegerField(default=0)
    due_end_offset_days = models.IntegerField(default=0)
    local_due_time = models.TimeField(default='09:00')
    frequency = models.CharField(
        max_length=12, choices=Frequency.choices, blank=True, default='',
    )
    interval = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    weekdays = models.JSONField(default=list, blank=True)
    season_start = models.CharField(max_length=5, blank=True, default='')
    season_end = models.CharField(max_length=5, blank=True, default='')
    variety = models.ForeignKey(
        'plants.PlantVariety', on_delete=models.PROTECT, null=True, blank=True,
        related_name='work_task_rules',
    )
    stage = models.ForeignKey(
        'plantings.GrowthStage', on_delete=models.PROTECT, null=True, blank=True,
        related_name='work_task_rules',
    )
    location = models.ForeignKey(
        'locations.Location', on_delete=models.PROTECT, null=True, blank=True,
        related_name='work_task_rules',
    )
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', editable=False,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'code'], name='work_rule_workspace_code_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(due_end_offset_days__gte=models.F('due_start_offset_days')),
                name='work_rule_due_offsets_ordered',
            ),
        ]

    def __str__(self):
        return self.name

    @staticmethod
    def _month_day(value):
        if not value:
            return True
        try:
            date.fromisoformat(f'2000-{value}')
        except ValueError:
            return False
        return True

    def clean(self):
        """Validate recurrence, season syntax, and workspace-scoped filters."""
        super().clean()
        errors = {}
        self.code = self.code.strip().lower()
        if not self.code:
            errors['code'] = 'A stable rule code is required.'
        if self.due_end_offset_days < self.due_start_offset_days:
            errors['due_end_offset_days'] = 'The due window cannot end before it starts.'
        if bool(self.season_start) != bool(self.season_end):
            errors['season_end'] = 'Provide both seasonal boundaries or neither.'
        elif not self._month_day(self.season_start) or not self._month_day(self.season_end):
            errors['season_start'] = 'Season boundaries must use MM-DD.'
        if any(not isinstance(day, int) or day < 0 or day > 6 for day in self.weekdays):
            errors['weekdays'] = 'Weekdays must be unique integers from 0 through 6.'
        elif len(set(self.weekdays)) != len(self.weekdays):
            errors['weekdays'] = 'Do not repeat a weekday.'
        if self.frequency == self.Frequency.WEEKLY and not self.weekdays:
            errors['weekdays'] = 'Weekly recurrence requires at least one weekday.'
        if self.trigger == self.Trigger.CALENDAR and not self.frequency:
            errors['frequency'] = 'Calendar work must repeat.'
        for field in ('variety', 'stage', 'location'):
            value = getattr(self, field, None)
            if value is not None and value.workspace_id != self.workspace_id:
                errors[field] = f'The {field} belongs to another workspace.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkTask(WorkspaceOwnedModel):
    """A manual task or acknowledged snapshot of generated work."""

    class Origin(models.TextChoices):
        """Whether work was entered by an operator or projected from a rule."""

        MANUAL = 'manual', 'Manual'
        GENERATED = 'generated', 'Generated'

    class Status(models.TextChoices):
        """Acknowledged task lifecycle states."""

        OPEN = 'open', 'Open'
        SNOOZED = 'snoozed', 'Snoozed'
        COMPLETED = 'completed', 'Completed'
        SKIPPED = 'skipped', 'Skipped'

    key = models.CharField(max_length=255)
    origin = models.CharField(max_length=16, choices=Origin.choices)
    rule = models.ForeignKey(
        WorkTaskRule, on_delete=models.PROTECT, null=True, blank=True,
        related_name='tasks',
    )
    task_type = models.CharField(max_length=32, choices=WorkTaskType.choices)
    title = models.CharField(max_length=255)
    notes = models.TextField(blank=True, default='')
    priority = models.PositiveSmallIntegerField(default=20)
    due_start = models.DateTimeField()
    due_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_work_tasks',
    )
    snoozed_until = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    recurrence = models.JSONField(default=dict, blank=True)
    parent = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='following_occurrences',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', editable=False,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_end', '-priority', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'key'], name='work_task_workspace_key_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(due_end__gte=models.F('due_start')),
                name='work_task_due_window_ordered',
            ),
        ]
        indexes = [
            models.Index(fields=['workspace', 'status', 'due_end'], name='work_queue_status_due_idx'),
            models.Index(fields=['workspace', 'assignee'], name='work_queue_assignee_idx'),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        errors = {}
        if self.due_end < self.due_start:
            errors['due_end'] = 'The due window cannot end before it starts.'
        if self.rule_id and self.rule.workspace_id != self.workspace_id:
            errors['rule'] = 'The rule belongs to another workspace.'
        if self.parent_id and self.parent.workspace_id != self.workspace_id:
            errors['parent'] = 'The previous occurrence belongs to another workspace.'
        if self.status == self.Status.SNOOZED and self.snoozed_until is None:
            errors['snoozed_until'] = 'A snoozed task requires a resume time.'
        if self.status == self.Status.COMPLETED and self.completed_at is None:
            errors['completed_at'] = 'A completed task requires a completion time.'
        if self.status == self.Status.SKIPPED and self.skipped_at is None:
            errors['skipped_at'] = 'A skipped task requires a skip time.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkTaskLink(models.Model):
    """A concrete work target or reviewed result retained as a snapshot."""

    class Role(models.TextChoices):
        """Whether a link describes affected work or a reviewed result."""

        TARGET = 'target', 'Target'
        RESULT = 'result', 'Result'

    task = models.ForeignKey(WorkTask, on_delete=models.PROTECT, related_name='links')
    role = models.CharField(max_length=12, choices=Role.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey('content_type', 'object_id')
    label = models.CharField(max_length=255)
    url = models.CharField(max_length=255, blank=True, default='')
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['role', 'content_type_id', 'object_id']
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'role', 'content_type', 'object_id'],
                name='work_task_link_unique',
            ),
        ]

    def clean(self):
        super().clean()
        target = self.target
        if target is None:
            raise ValidationError({'object_id': 'The linked record does not exist.'})
        workspace_id = getattr(target, 'workspace_id', None)
        if workspace_id is None and hasattr(target, 'workspace'):
            workspace_id = target.workspace_id
        if workspace_id is not None and workspace_id != self.task.workspace_id:
            raise ValidationError({'object_id': 'The linked record belongs to another workspace.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkTaskHistory(models.Model):
    """One immutable chronological task action."""

    task = models.ForeignKey(WorkTask, on_delete=models.PROTECT, related_name='history')
    action = models.CharField(max_length=32)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    reason = models.TextField(blank=True, default='')
    changes = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True, default='')
    created = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['created', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'idempotency_key'],
                condition=~models.Q(idempotency_key=''),
                name='work_history_task_idempotent',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Task history is immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Task history cannot be deleted.')
