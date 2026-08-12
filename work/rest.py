"""REST resources for rules, the combined work queue, and task actions."""

# DRF viewsets and serializers have framework-defined small method signatures.
# pylint: disable=too-many-ancestors,missing-function-docstring,missing-class-docstring,duplicate-code,abstract-method

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .models import WorkTask, WorkTaskHistory, WorkTaskLink, WorkTaskRule, WorkTaskType
from .projections import ProjectedTask, projected_tasks
from .services import acknowledge_projection, act_on_task, create_manual_task


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class WorkRuleSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = WorkTaskRule
        fields = [
            'pk', 'code', 'name', 'task_type', 'trigger', 'active', 'priority',
            'due_start_offset_days', 'due_end_offset_days', 'local_due_time',
            'frequency', 'interval', 'weekdays', 'season_start', 'season_end',
            'variety', 'stage', 'location', 'default_assignee', 'notes',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']

    workspace_field_lookups = {
        'variety': 'workspace', 'stage': 'workspace', 'location': 'workspace',
    }


class HistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True, allow_null=True)

    class Meta:
        model = WorkTaskHistory
        fields = ['pk', 'action', 'actor', 'actor_name', 'reason', 'changes', 'created']


class LinkSerializer(serializers.ModelSerializer):
    target_type = serializers.CharField(source='content_type.model', read_only=True)
    active_health_alerts = serializers.SerializerMethodField()

    class Meta:
        model = WorkTaskLink
        fields = [
            'role', 'target_type', 'object_id', 'label', 'url', 'snapshot',
            'active_health_alerts',
        ]

    def get_active_health_alerts(self, link):
        from health.availability import target_alert_count  # pylint: disable=import-outside-toplevel

        return target_alert_count(link.target)


class WorkTaskSerializer(serializers.ModelSerializer):
    assignee_name = serializers.CharField(source='assignee.username', read_only=True, allow_null=True)
    links = LinkSerializer(many=True, read_only=True)
    history = HistorySerializer(many=True, read_only=True)

    class Meta:
        model = WorkTask
        fields = [
            'pk', 'key', 'origin', 'rule', 'task_type', 'title', 'notes',
            'priority', 'due_start', 'due_end', 'status', 'assignee',
            'assignee_name', 'snoozed_until', 'completed_at', 'skipped_at',
            'source_snapshot', 'recurrence', 'links', 'history', 'created', 'updated',
        ]
        read_only_fields = [
            'pk', 'key', 'origin', 'rule', 'status', 'snoozed_until',
            'completed_at', 'skipped_at', 'source_snapshot', 'created', 'updated',
        ]


class ManualTaskSerializer(serializers.Serializer):
    task_type = serializers.ChoiceField(choices=WorkTaskType.choices)
    title = serializers.CharField(max_length=255)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    priority = serializers.IntegerField(min_value=1, max_value=100, default=20)
    due_start = serializers.DateTimeField()
    due_end = serializers.DateTimeField()
    assignee = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True), required=False,
        allow_null=True,
    )
    recurrence = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if attrs['due_end'] < attrs['due_start']:
            raise serializers.ValidationError({'due_end': 'The due window cannot end before it starts.'})
        recurrence = attrs['recurrence']
        if recurrence and recurrence.get('frequency') not in {
                WorkTaskRule.Frequency.DAILY, WorkTaskRule.Frequency.WEEKLY}:
            raise serializers.ValidationError({'recurrence': 'Choose daily or weekly recurrence.'})
        if recurrence.get('frequency') == WorkTaskRule.Frequency.WEEKLY:
            weekdays = recurrence.get('weekdays', [])
            if not weekdays or any(day not in range(7) for day in weekdays):
                raise serializers.ValidationError({
                    'recurrence': 'Weekly recurrence requires weekdays from 0 through 6.',
                })
        return attrs


class TaskActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['assign', 'claim', 'snooze', 'complete', 'skip', 'reopen'])
    assignee = serializers.IntegerField(required=False, allow_null=True)
    until = serializers.DateTimeField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    results = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=64, default='')


def _projected_data(task):
    return {
        'pk': None, 'key': task.key, 'origin': task.origin, 'rule': task.rule.pk,
        'task_type': task.task_type, 'title': task.title, 'notes': '',
        'priority': task.priority, 'due_start': task.due_start,
        'due_end': task.due_end, 'status': task.status,
        'assignee': task.assignee.pk if task.assignee else None,
        'assignee_name': task.assignee.username if task.assignee else None,
        'snoozed_until': None, 'completed_at': None, 'skipped_at': None,
        'source_snapshot': task.source_snapshot, 'recurrence': {},
        'links': [{
            'role': WorkTaskLink.Role.TARGET,
            'target_type': row.target._meta.model_name,
            'object_id': row.target.pk, 'label': row.label, 'url': row.url,
            'snapshot': {},
            'active_health_alerts': _target_health_alert_count(row.target),
        } for row in task.targets],
        'history': [], 'created': None, 'updated': None,
    }


def _target_health_alert_count(target):
    from health.availability import target_alert_count  # pylint: disable=import-outside-toplevel

    return target_alert_count(target)


def _effective_status(task, now):
    if task.status == WorkTask.Status.SNOOZED and task.snoozed_until <= now:
        return WorkTask.Status.OPEN
    return task.status


def _in_view(task, view, workspace, now):
    effective = _effective_status(task, now)
    local = now.astimezone(ZoneInfo(workspace.timezone))
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    week_start = day_start - timedelta(days=day_start.weekday())
    week_end = week_start + timedelta(days=7)
    if view == 'completed':
        return effective in {WorkTask.Status.COMPLETED, WorkTask.Status.SKIPPED}
    if view == 'snoozed':
        return effective == WorkTask.Status.SNOOZED
    if effective != WorkTask.Status.OPEN:
        return False
    if view == 'overdue':
        return task.due_end < now
    if view == 'week':
        return task.due_start < week_end and task.due_end >= week_start
    return task.due_start < day_end and task.due_end >= day_start


def _has_target(task, target_type, object_id):
    """Match projected or persisted concrete links without trusting snapshots."""
    if isinstance(task, ProjectedTask):
        return any(
            link.target._meta.model_name == target_type and link.target.pk == object_id
            for link in task.targets
        )
    return task.links.filter(
        role=WorkTaskLink.Role.TARGET,
        content_type__model=target_type,
        object_id=object_id,
    ).exists()


class NurseryWorkMixin(RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin):
    required_workspace_modes = (Workspace.Mode.NURSERY,)


class WorkRuleViewSet(NurseryWorkMixin, viewsets.ModelViewSet):
    queryset = WorkTaskRule.objects.select_related('variety', 'stage', 'location', 'default_assignee')
    serializer_class = WorkRuleSerializer

    def perform_create(self, serializer):
        serializer.save(workspace=self.get_current_workspace(), created_by=self.request.user)


class WorkTaskViewSet(NurseryWorkMixin, viewsets.GenericViewSet):
    queryset = WorkTask.objects.select_related('rule', 'assignee').prefetch_related(
        'links__content_type', 'history__actor',
    )
    serializer_class = WorkTaskSerializer

    def list(self, request):
        workspace = self.get_current_workspace()
        now = timezone.now()
        view = request.query_params.get('view', 'today')
        rows = list(self.get_queryset()) + list(projected_tasks(workspace))
        rows = [row for row in rows if _in_view(row, view, workspace, now)]
        for field in ('task_type', 'priority'):
            value = request.query_params.get(field)
            if value:
                rows = [row for row in rows if str(getattr(row, field)) == value]
        assignee = request.query_params.get('assignee')
        if assignee:
            rows = [row for row in rows if row.assignee and str(row.assignee.pk) == assignee]
        for parameter, target_type in (('batch', 'productionbatch'), ('location', 'location')):
            value = request.query_params.get(parameter)
            if value and value.isdigit():
                rows = [row for row in rows if _has_target(row, target_type, int(value))]
        rows.sort(key=lambda row: (row.due_end, -row.priority, row.key))
        return Response([
            _projected_data(row) if isinstance(row, ProjectedTask) else WorkTaskSerializer(row).data
            for row in rows
        ])

    def create(self, request):
        serializer = ManualTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        assignee = values.pop('assignee', None)
        task = create_manual_task(
            self.get_current_workspace(), request.user,
            {**values, 'assignee': assignee},
        )
        return Response(WorkTaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='acknowledge')
    def acknowledge(self, request):
        key = serializers.CharField().run_validation(request.data.get('key'))
        try:
            task = acknowledge_projection(self.get_current_workspace(), request.user, key)
        except DjangoValidationError as error:
            return Response(_errors(error), status=status.HTTP_409_CONFLICT)
        return Response(WorkTaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='act')
    def act(self, request, pk=None):
        serializer = TaskActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        action_name = values.pop('action')
        try:
            task = act_on_task(
                self.get_current_workspace(), request.user, pk, action_name, values,
            )
        except DjangoValidationError as error:
            return Response(_errors(error), status=status.HTTP_400_BAD_REQUEST)
        return Response(WorkTaskSerializer(task).data)


class AssigneeViewSet(NurseryWorkMixin, viewsets.ViewSet):
    queryset = get_user_model().objects.none()

    def list(self, request):
        users = get_user_model().objects.filter(is_active=True).order_by('username', 'pk')
        return Response([{'pk': user.pk, 'username': user.username} for user in users])
