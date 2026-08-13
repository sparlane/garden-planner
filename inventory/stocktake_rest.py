"""REST workflow for frozen, blind, reviewed nursery stocktakes."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from labels.models import LabelCode
from locations.models import Location
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import (
    Stocktake,
    StocktakeAttachment,
    StocktakeTarget,
    StocktakeVariance,
)
from .ledger import post_stocktake, reverse_stocktake
from .stocktakes import (
    approve_stocktake,
    begin_review,
    open_stocktake,
    record_count,
    request_recount,
    resolve_variance,
    scope_rows,
)


def _errors(exc):
    return exc.message_dict if hasattr(exc, 'message_dict') else {'detail': exc.messages}


def _run(service, *args, **kwargs):
    try:
        return service(*args, **kwargs)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(_errors(exc)) from exc


class ScopeSerializer(CurrentWorkspaceSerializerMixin, serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the filters whose resolved targets are frozen at creation."""

    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all())
    include_descendants = serializers.BooleanField(required=False, default=False)
    target_types = serializers.ListField(
        child=serializers.ChoiceField(choices=StocktakeTarget.TargetType.choices),
        required=False,
    )
    item = serializers.IntegerField(min_value=1, required=False)
    category = serializers.CharField(required=False)
    variety = serializers.IntegerField(min_value=1, required=False)
    stage = serializers.IntegerField(min_value=1, required=False)
    tray_state = serializers.CharField(required=False)
    workspace_field_lookups = {'location': 'workspace'}

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        values['location'] = values['location'].pk
        return values


class StocktakeCreateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Open a session from reviewed scope input."""

    scope = ScopeSerializer()
    blind = serializers.BooleanField(required=False, default=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class CountSerializer(CurrentWorkspaceSerializerMixin, serializers.Serializer):  # pylint: disable=abstract-method
    """Validate one quantity entry or resolved identity scan."""

    target = serializers.IntegerField(min_value=1)
    counted_quantity = serializers.DecimalField(
        max_digits=24, decimal_places=9, min_value=Decimal('0'), required=False,
    )
    observed_location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), required=False, allow_null=True,
    )
    observed_state = serializers.CharField(required=False, allow_blank=True, default='')
    code_snapshot = serializers.CharField(required=False, allow_blank=True, default='')
    resolved_identity = serializers.JSONField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    workspace_field_lookups = {'observed_location': 'workspace'}


class ScanCountSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Resolve a label inside the current workspace before recording it."""

    code = serializers.CharField(trim_whitespace=True, allow_blank=False)
    counted_quantity = serializers.DecimalField(
        max_digits=24, decimal_places=9, min_value=Decimal('0'), required=False,
    )
    observed_state = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class ReasonSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Require an audit explanation for a review transition."""

    reason = serializers.CharField(trim_whitespace=True, allow_blank=False)


class RecountSerializer(ReasonSerializer):  # pylint: disable=abstract-method
    """Identify the reviewed target that needs another count."""

    target = serializers.IntegerField(min_value=1)


class VarianceResolutionSerializer(ReasonSerializer):  # pylint: disable=abstract-method
    """Capture an explicit correction rather than guessing from variance type."""

    variance = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=(
        'no_change', 'adjust', 'move', 'lost', 'state_correct',
    ))
    payload = serializers.JSONField(required=False)
    accept_conflict = serializers.BooleanField(required=False, default=False)


class AttachmentSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Attach an externally hosted image or document to retained evidence."""

    target = serializers.IntegerField(min_value=1, required=False)
    url = serializers.URLField(max_length=2048)
    label = serializers.CharField(required=False, allow_blank=True, default='')


def _count_data(count):
    if count is None:
        return None
    return {
        'pk': count.pk,
        'counted_quantity': (
            str(count.counted_quantity) if count.counted_quantity is not None else None
        ),
        'observed_location': count.observed_location_id,
        'observed_state': count.observed_state,
        'code_snapshot': count.code_snapshot,
        'resolved_identity': count.resolved_identity,
        'notes': count.notes,
        'counter': count.counter_id,
        'created': count.created,
    }


def _variance_data(variance):
    return {
        'pk': variance.pk, 'kind': variance.kind,
        'expected': variance.expected, 'observed': variance.observed,
        'source_changed': variance.source_changed,
        'current_revision': variance.current_revision,
        'conflict_resolution': variance.conflict_resolution,
        'conflict_reason': variance.conflict_reason,
        'resolution_action': variance.resolution_action,
        'resolution_payload': variance.resolution_payload,
        'resolution_reason': variance.resolution_reason,
        'resolved_by': variance.resolved_by_id,
        'resolved_at': variance.resolved_at,
    }


def _target_data(target, reveal_expected):
    return {
        'pk': target.pk,
        'target_type': target.target_type,
        'target_key': target.target_key,
        'target_object_id': target.target_object_id,
        'display': target.display,
        'expected_location': target.expected_location_id,
        'expected_quantity': (
            str(target.expected_quantity)
            if reveal_expected and target.expected_quantity is not None else None
        ),
        'expected_state': target.expected_state if reveal_expected else '',
        'expected_snapshot': target.expected_snapshot if reveal_expected else {},
        'unexpected': target.unexpected,
        'count_status': target.count_status,
        'accepted_count': _count_data(target.accepted_count),
        'counts': [_count_data(count) for count in target.counts.all()],
        'variances': [_variance_data(row) for row in target.variances.all()],
    }


def stocktake_data(stocktake):
    """Serialize workflow state while preserving blind-count behavior."""
    if not stocktake.targets.exists():
        from .ledger_rest import StocktakeSerializer  # pylint: disable=import-outside-toplevel
        return StocktakeSerializer(stocktake).data
    reveal = not stocktake.blind or stocktake.status in {
        Stocktake.Status.REVIEW, Stocktake.Status.APPROVED,
        Stocktake.Status.POSTED, Stocktake.Status.REVERSED,
    }
    targets = list(stocktake.targets.all())
    counted = sum(target.accepted_count_id is not None for target in targets)
    return {
        'pk': stocktake.pk, 'status': stocktake.status,
        'blind': stocktake.blind, 'scope': stocktake.scope,
        'notes': stocktake.notes, 'counted_at': stocktake.counted_at,
        'created_by': stocktake.created_by_id,
        'reviewed_by': stocktake.reviewed_by_id,
        'reviewed_at': stocktake.reviewed_at,
        'approved_by': stocktake.approved_by_id,
        'approved_at': stocktake.approved_at,
        'posted_by': stocktake.posted_by_id,
        'posted_at': stocktake.posted_at,
        'reversed_by': stocktake.reversed_by_id,
        'reversed_at': stocktake.reversed_at,
        'progress': {'counted': counted, 'total': len(targets)},
        'targets': [_target_data(target, reveal) for target in targets],
        'attachments': [
            {'pk': row.pk, 'target': row.target_id, 'url': row.url,
             'label': row.label, 'created_by': row.created_by_id,
             'created': row.created}
            for row in stocktake.attachments.all()
        ],
    }


class NurseryStocktakeViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ViewSet,
):
    """Count, review, and approve stock without bypassing domain ledgers."""

    def get_queryset(self):
        return Stocktake.objects.filter(workspace=self.get_current_workspace()).select_related(
            'created_by', 'reviewed_by', 'approved_by', 'posted_by', 'reversed_by',
        ).prefetch_related(
            'targets__accepted_count', 'targets__counts', 'targets__variances',
            'attachments',
        )

    def _get(self, pk):
        try:
            return self.get_queryset().get(pk=pk)
        except Stocktake.DoesNotExist as exc:
            from rest_framework.exceptions import NotFound  # pylint: disable=import-outside-toplevel
            raise NotFound('Stocktake not found.') from exc

    def list(self, request):  # pylint: disable=unused-argument
        """List session summaries without returning every count sheet."""
        rows = []
        for stocktake in self.get_queryset():
            data = stocktake_data(stocktake)
            data.pop('targets')
            data.pop('attachments')
            rows.append(data)
        return Response(rows)

    def retrieve(self, request, pk=None):  # pylint: disable=unused-argument
        """Return one blind count sheet or revealed review document."""
        return Response(stocktake_data(self._get(pk)))

    def create(self, request):
        """Resolve and freeze scope immediately when a session opens."""
        if 'lines' in request.data:
            from .ledger_rest import StocktakeSerializer  # pylint: disable=import-outside-toplevel
            serializer = StocktakeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            stocktake = serializer.save(
                workspace=self.get_current_workspace(), created_by=request.user,
            )
            return Response(stocktake_data(stocktake), status=status.HTTP_201_CREATED)
        serializer = StocktakeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stocktake = _run(
            open_stocktake, self.get_current_workspace(), request.user,
            **serializer.validated_data,
        )
        return Response(stocktake_data(self._get(stocktake.pk)), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='scope-preview')
    def scope_preview(self, request):
        """Show target totals without creating audit records."""
        serializer = ScopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rows = _run(scope_rows, self.get_current_workspace(), serializer.validated_data)
        totals = {choice: 0 for choice in StocktakeTarget.TargetType.values}
        for row in rows:
            totals[row['target_type']] += 1
        return Response({'total': len(rows), 'by_type': totals})

    @action(detail=True, methods=['post'])
    def count(self, request, pk=None):
        """Append one manual quantity or identity count."""
        stocktake = self._get(pk)
        serializer = CountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        target_id = values.pop('target')
        count = _run(record_count, stocktake, request.user, target_id, **values)
        return Response(_count_data(count), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='scan-count')
    def scan_count(self, request, pk=None):
        """Resolve and retain one scanned identity and exact code snapshot."""
        stocktake = self._get(pk)
        serializer = ScanCountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code_value = serializer.validated_data.pop('code').strip().upper()
        code = LabelCode.objects.select_related(
            'identity__target_content_type',
        ).filter(workspace=self.get_current_workspace(), code=code_value).first()
        if code is None:
            if LabelCode.objects.filter(code=code_value).exists():
                raise serializers.ValidationError({'code': 'This code belongs to another workspace.'})
            raise serializers.ValidationError({'code': 'No label uses this code.'})
        identity = code.identity
        mapping = {
            'seedtray': StocktakeTarget.TargetType.TRAY,
            'plantcohort': StocktakeTarget.TargetType.COHORT,
            'specificplant': StocktakeTarget.TargetType.PLANT,
        }
        target_type = mapping.get(identity.target_content_type.model)
        if target_type is None:
            raise serializers.ValidationError({'code': 'This identity cannot be counted in a stocktake.'})
        key = f'{target_type}:{identity.target_object_id}'
        target = stocktake.targets.filter(target_key=key).first()
        if target is None:
            with transaction.atomic():
                target = StocktakeTarget.objects.create(
                    stocktake=stocktake, target_type=target_type,
                    target_key=key, target_object_id=identity.target_object_id,
                    display=identity.target_snapshot.get('display', key),
                    expected_snapshot={}, source_revision='', unexpected=True,
                )
        values = serializer.validated_data
        count = _run(
            record_count, stocktake, request.user, target.pk,
            code_snapshot=code.code,
            resolved_identity={
                'identity': identity.pk, 'object_id': identity.target_object_id,
                'target_type': identity.target_content_type.model,
                'code_status': code.status,
            },
            **values,
        )
        return Response(_count_data(count), status=status.HTTP_201_CREATED)

    def _transition(self, pk, allowed, next_status):
        stocktake = self._get(pk)
        if stocktake.status not in allowed:
            raise serializers.ValidationError({'status': 'This transition is not available.'})
        Stocktake.objects.filter(pk=stocktake.pk).update(status=next_status, updated=timezone.now())
        return Response(stocktake_data(self._get(pk)))

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):  # pylint: disable=unused-argument
        """Pause an open mobile count without losing progress."""
        return self._transition(pk, {Stocktake.Status.OPEN}, Stocktake.Status.PAUSED)

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):  # pylint: disable=unused-argument
        """Resume a paused count."""
        return self._transition(pk, {Stocktake.Status.PAUSED}, Stocktake.Status.OPEN)

    @action(detail=True, methods=['post'], url_path='begin-review')
    def review(self, request, pk=None):
        """Reveal frozen expectations and calculate review variances."""
        stocktake = _run(begin_review, self._get(pk), request.user)
        return Response(stocktake_data(self._get(stocktake.pk)))

    @action(detail=True, methods=['post'], url_path='request-recount')
    def recount(self, request, pk=None):
        """Return one reviewed target to blind counting."""
        serializer = RecountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stocktake = self._get(pk)
        try:
            target = stocktake.targets.get(pk=serializer.validated_data['target'])
        except StocktakeTarget.DoesNotExist as exc:
            raise serializers.ValidationError({'target': 'Target not found.'}) from exc
        stocktake = _run(
            request_recount, stocktake, target, request.user,
            serializer.validated_data['reason'],
        )
        return Response(stocktake_data(self._get(stocktake.pk)))

    @action(detail=True, methods=['post'], url_path='resolve-variance')
    def resolve(self, request, pk=None):
        """Record one explicit reviewer-selected correction."""
        serializer = VarianceResolutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stocktake = self._get(pk)
        try:
            variance = StocktakeVariance.objects.get(
                pk=serializer.validated_data.pop('variance'),
                target__stocktake=stocktake,
            )
        except StocktakeVariance.DoesNotExist as exc:
            raise serializers.ValidationError({'variance': 'Variance not found.'}) from exc
        variance = _run(resolve_variance, variance, request.user, **serializer.validated_data)
        return Response(_variance_data(variance))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):  # pylint: disable=unused-argument
        """Approve a fully resolved review without changing source records."""
        stocktake = _run(approve_stocktake, self._get(pk), request.user)
        return Response(stocktake_data(self._get(stocktake.pk)))

    @action(detail=True, methods=['post'])
    def post(self, request, pk=None):  # pylint: disable=unused-argument
        """Retain the legacy lot-only posting action during the transition."""
        stocktake = self._get(pk)
        if stocktake.targets.exists():
            raise serializers.ValidationError({'status': 'Approve this session before posting.'})
        stocktake, _movements = _run(post_stocktake, stocktake, request.user)
        return Response(stocktake_data(stocktake))

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        """Reverse a legacy lot-only stocktake through its existing service."""
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stocktake = self._get(pk)
        if stocktake.targets.exists():
            raise serializers.ValidationError({'status': 'Use reviewed-session reversal.'})
        stocktake, _movements = _run(
            reverse_stocktake, stocktake, request.user,
            serializer.validated_data['reason'],
        )
        return Response(stocktake_data(stocktake))

    @action(detail=True, methods=['post'], url_path='attachments')
    def attach(self, request, pk=None):
        """Retain a URL-based photo or document with the session."""
        serializer = AttachmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stocktake = self._get(pk)
        target_id = serializer.validated_data.pop('target', None)
        target = None
        if target_id:
            try:
                target = stocktake.targets.get(pk=target_id)
            except StocktakeTarget.DoesNotExist as exc:
                raise serializers.ValidationError({'target': 'Target not found.'}) from exc
        row = StocktakeAttachment.objects.create(
            stocktake=stocktake, target=target, created_by=request.user,
            **serializer.validated_data,
        )
        return Response({'pk': row.pk, 'url': row.url}, status=status.HTTP_201_CREATED)
