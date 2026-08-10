"""REST resources for label templates, printing, and scan resolution."""

from urllib.parse import unquote, urlparse

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from plantings.lifecycle import derive_state
from workspaces.models import get_current_workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .models import LabelCode, LabelIdentity, LabelPrintItem, LabelPrintJob, LabelTemplate
from .services import normalize_code, replace_code, void_code


LABEL_FIELDS = {
    'display',
    'variety',
    'batch',
    'sowing_date',
    'expected_ready',
    'code',
    'print_date',
}
DIMENSION_FIELDS = {
    'label_width_mm',
    'label_height_mm',
    'page_width_mm',
    'page_height_mm',
    'margin_mm',
    'gap_mm',
}
TARGET_ROUTES = {
    ('plantings', 'specificplant'): '/plantings/plants/{pk}',
    ('seedtrays', 'seedtray'): '/seedtrays/{pk}',
    ('plantings', 'productionbatch'): '/plantings/batches/{pk}',
    ('locations', 'location'): '/locations',
    ('garden', 'gardenarea'): '/gardens/{pk}',
}


def _model_error(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _key(identity):
    return (identity.target_content_type.app_label, identity.target_content_type.model)


def _active_code(identity):
    return identity.codes.filter(status=LabelCode.Status.ACTIVE).first()


def _target_values(identity, code=None):
    """Project useful label fields without making them a second source of truth."""
    target = identity.target
    values = dict(identity.target_snapshot)
    values['identity'] = identity.pk
    values['target_type'] = identity.target_content_type.model
    values['object_id'] = identity.target_object_id
    values['code'] = code.code if code else None
    values['print_date'] = timezone.localdate().isoformat()
    if target is None:
        return values
    key = _key(identity)
    if key == ('plantings', 'specificplant'):
        planting = target.cell_planting.seed_tray_planting
        variety = planting.batch.variety
        values.update({
            'display': f'{variety.plant.name} — {variety.name}',
            'variety': variety.name,
            'batch': planting.batch.code,
            'sowing_date': planting.planted.date().isoformat(),
        })
    elif key == ('plantings', 'productionbatch'):
        values.update({
            'display': target.code,
            'variety': target.variety.name,
            'batch': target.code,
            'sowing_date': target.actual_start.date().isoformat() if target.actual_start else target.planned_start,
        })
    elif key == ('seedtrays', 'seedtray'):
        open_generation = target.generations.filter(status='open').first()
        values['display'] = f'Tray {target.pk} — {target.model.identifier}'
        if open_generation:
            sowings = list(open_generation.sowings.select_related('batch__variety'))
            batches = sorted({sowing.batch.code for sowing in sowings})
            varieties = sorted({sowing.batch.variety.name for sowing in sowings})
            dates = sorted({sowing.planted.date().isoformat() for sowing in sowings})
            values['batch'] = batches[0] if len(batches) == 1 else ('Mixed' if batches else None)
            values['variety'] = varieties[0] if len(varieties) == 1 else ('Mixed' if varieties else None)
            values['sowing_date'] = dates[0] if len(dates) == 1 else ('Mixed' if dates else None)
    elif key == ('locations', 'location'):
        values['display'] = target.name
    elif key == ('garden', 'gardenarea'):
        values['display'] = target.name
    return values


def _target_active(identity):
    target = identity.target
    if not identity.active or target is None:
        return False
    key = _key(identity)
    if key == ('locations', 'location'):
        return target.active
    if key == ('seedtrays', 'seedtray'):
        return target.inventory_unit.active
    return True


def _resolution(code, workspace):
    """Build the safe scan contract for a code visible to this workspace."""
    if code.workspace_id != workspace.pk:
        return {'status': 'wrong_workspace', 'message': 'This code belongs to another workspace.'}
    identity = code.identity
    current = _active_code(identity)
    resolution_status = code.status
    if code.status == LabelCode.Status.VOID or not _target_active(identity):
        resolution_status = 'inactive'
    key = _key(identity)
    route = TARGET_ROUTES.get(key)
    capabilities = ['inspect', 'print'] if resolution_status == LabelCode.Status.ACTIVE else ['inspect']
    if resolution_status == LabelCode.Status.ACTIVE and key == ('plantings', 'specificplant'):
        summary = derive_state(identity.target.lifecycle_events.all())
        if summary.state in ('growing', 'available', 'retained'):
            capabilities.append('bulk_select')
    return {
        'status': resolution_status,
        'message': {
            'active': 'Code resolved.',
            'replaced': 'This label was replaced; use the current code.',
            'inactive': 'This label is inactive.',
        }[resolution_status],
        'code': code.code,
        'current_code': current.code if current else None,
        'target': _target_values(identity, current or code),
        'deep_link': route.format(pk=identity.target_object_id) if route else None,
        'capabilities': capabilities,
    }


class LabelTemplateSerializer(serializers.ModelSerializer):
    """Validate reusable physical label layout configuration."""

    class Meta:
        model = LabelTemplate
        fields = ['pk', 'name', 'format', 'payload_mode', 'layout', 'fields', 'dimensions', 'built_in', 'active', 'created', 'updated']
        read_only_fields = ['pk', 'built_in', 'created', 'updated']

    def validate_fields(self, value):
        """Require known fields once each in their desired display order."""
        if not isinstance(value, list) or not value or len(value) != len(set(value)):
            raise serializers.ValidationError('Choose a non-empty ordered list without duplicates.')
        unknown = set(value) - LABEL_FIELDS
        if unknown:
            raise serializers.ValidationError(f'Unknown label fields: {", ".join(sorted(unknown))}.')
        return value

    def validate_dimensions(self, value):
        """Require positive label dimensions and non-negative page spacing."""
        if not isinstance(value, dict):
            raise serializers.ValidationError('Dimensions must be an object.')
        unknown = set(value) - DIMENSION_FIELDS
        if unknown:
            raise serializers.ValidationError(f'Unknown dimensions: {", ".join(sorted(unknown))}.')
        for required in ('label_width_mm', 'label_height_mm'):
            if required not in value or not isinstance(value[required], (int, float)) or value[required] <= 0:
                raise serializers.ValidationError(f'{required} must be a positive number.')
        for key, number in value.items():
            if not isinstance(number, (int, float)) or number < 0:
                raise serializers.ValidationError(f'{key} must be a non-negative number.')
        return value

    def validate(self, attrs):
        format_name = attrs.get('format', getattr(self.instance, 'format', None))
        payload_mode = attrs.get('payload_mode', getattr(self.instance, 'payload_mode', None))
        if format_name == LabelTemplate.Format.CODE128 and payload_mode != LabelTemplate.PayloadMode.CODE:
            raise serializers.ValidationError({'payload_mode': 'Code 128 labels always contain the bare code.'})
        if self.instance and self.instance.built_in:
            editable = set(attrs) - {'active'}
            if editable:
                raise serializers.ValidationError('Clone a built-in template before changing it.')
        return attrs


class LabelTemplateViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Manage current-workspace print templates without deleting built-ins."""

    queryset = LabelTemplate.objects.all()
    serializer_class = LabelTemplateSerializer

    def perform_destroy(self, instance):
        if instance.built_in:
            raise ValidationError({'template': 'Built-in templates cannot be deleted.'})
        instance.active = False
        instance.save(update_fields=['active', 'updated'])


class LabelIdentityViewSet(CurrentWorkspaceViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """List printable identities without exposing generic target internals."""

    queryset = LabelIdentity.objects.select_related('target_content_type').prefetch_related('codes')

    def list(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        queryset = self.get_queryset().filter(active=True)
        target_type = request.query_params.get('target_type')
        if target_type:
            queryset = queryset.filter(target_content_type__model=target_type)
        rows = []
        for identity in queryset:
            code = _active_code(identity)
            if code:
                rows.append(_target_values(identity, code))
        return Response(rows)


class PrintTargetSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the identities and template selected for one print operation."""

    identities = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=5000)
    template = serializers.PrimaryKeyRelatedField(queryset=LabelTemplate.objects.all())
    payload_mode = serializers.ChoiceField(choices=LabelTemplate.PayloadMode.choices, required=False)

    def validate(self, attrs):
        workspace = get_current_workspace()
        template = attrs['template']
        if template.workspace_id != workspace.pk:
            raise serializers.ValidationError({'template': 'The template belongs to another workspace.'})
        if template.format == LabelTemplate.Format.CODE128 and attrs.get('payload_mode', template.payload_mode) != LabelTemplate.PayloadMode.CODE:
            raise serializers.ValidationError({'payload_mode': 'Code 128 labels always contain the bare code.'})
        identities = list(LabelIdentity.objects.filter(workspace=workspace, pk__in=attrs['identities']).select_related('target_content_type'))
        if len(identities) != len(set(attrs['identities'])):
            raise serializers.ValidationError({'identities': 'One or more identities are unavailable or duplicated.'})
        attrs['identities'] = identities
        return attrs


def _template_snapshot(template, payload_mode):
    return {
        'name': template.name,
        'format': template.format,
        'payload_mode': payload_mode,
        'layout': template.layout,
        'fields': template.fields,
        'dimensions': template.dimensions,
    }


def _print_items(request, values):
    template = values['template']
    payload_mode = values.get('payload_mode', template.payload_mode)
    base_url = request.build_absolute_uri('/').rstrip('/')
    items = []
    for position, identity in enumerate(values['identities']):
        code = _active_code(identity)
        if code is None or not _target_active(identity):
            raise ValidationError({'identities': f'Identity {identity.pk} has no active printable code.'})
        payload = code.code if payload_mode == 'code' else f'{base_url}/#/scan/{code.code}'
        items.append({
            'position': position,
            'identity': identity,
            'code': code,
            'payload': payload,
            'target_snapshot': _target_values(identity, code),
            'is_reprint': LabelPrintItem.objects.filter(identity=identity, job__printed_at__isnull=False).exists(),
        })
    return _template_snapshot(template, payload_mode), items


def _print_response(snapshot, items, job=None):
    return {
        'job': job.pk if job else None,
        'printed_at': job.printed_at if job else None,
        'template': snapshot,
        'items': [
            {
                'position': item['position'],
                'identity': item['identity'].pk,
                'code': item['code'].code,
                'payload': item['payload'],
                'target': item['target_snapshot'],
                'is_reprint': item['is_reprint'],
            }
            for item in items
        ],
    }


class LabelPrintJobViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Preview, initiate, and inspect immutable physical print jobs."""

    queryset = LabelPrintJob.objects.prefetch_related('items')

    def list(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        jobs = self.get_queryset()[:100]
        return Response([{'pk': job.pk, 'created': job.created, 'printed_at': job.printed_at, 'items': job.items.count()} for job in jobs])

    def retrieve(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        job = self.get_object()
        items = [
            {'position': item.position, 'identity': item.identity, 'code': item.code, 'payload': item.payload, 'target_snapshot': item.target_snapshot, 'is_reprint': item.is_reprint}
            for item in job.items.select_related('identity', 'code')
        ]
        return Response(_print_response(job.template_snapshot, items, job))

    @action(detail=False, methods=['post'])
    def preview(self, request):
        """Render print data without creating an audit record."""
        serializer = PrintTargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snapshot, items = _print_items(request, serializer.validated_data)
        return Response(_print_response(snapshot, items))

    @transaction.atomic
    def create(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """Freeze one print job and every code and target value it contains."""
        serializer = PrintTargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        snapshot, items = _print_items(request, values)
        job = LabelPrintJob.objects.create(
            workspace=self.get_current_workspace(),
            template=values['template'],
            template_snapshot=snapshot,
            created_by=request.user,
        )
        for item in items:
            LabelPrintItem.objects.create(job=job, **item)
        return Response(_print_response(snapshot, items, job), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def printed(self, request, pk=None):  # pylint: disable=unused-argument
        """Record the first time the operator initiates the browser print dialog."""
        job = self.get_object()
        if job.printed_at is None:
            job.printed_at = timezone.now()
            job.save(update_fields=['printed_at'])
        return Response({'pk': job.pk, 'printed_at': job.printed_at})


class ReasonSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Require an audit explanation for retiring a physical code."""

    reason = serializers.CharField(trim_whitespace=True, allow_blank=False)


class LabelCodeViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Resolve code history and expose explicit replacement or void actions."""

    queryset = LabelCode.objects.select_related('identity', 'identity__target_content_type', 'replacement')

    def retrieve(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        code = self.get_object()
        return Response(_resolution(code, self.get_current_workspace()))

    def _retire(self, request, service):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = service(self.get_object(), request.user, serializer.validated_data['reason'])
        except DjangoValidationError as exc:
            raise ValidationError(_model_error(exc)) from exc
        return Response(_resolution(result, self.get_current_workspace()))

    @action(detail=True, methods=['post'])
    def replace(self, request, pk=None):  # pylint: disable=unused-argument
        """Retire an active code and return its newly issued successor."""
        return self._retire(request, replace_code)

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):  # pylint: disable=unused-argument
        """Retire an active code without issuing a successor."""
        return self._retire(request, void_code)


class ResolveLabelView(APIView):
    """Resolve a typed code or QR deep link inside the current workspace."""

    def get(self, request):
        """Return an explicit safe outcome for every scanner input."""
        raw = request.query_params.get('value', '')
        parsed = urlparse(raw)
        if parsed.fragment:
            candidate = parsed.fragment.rstrip('/').split('/')[-1]
        else:
            candidate = parsed.path.rstrip('/').split('/')[-1] if '://' in raw else raw
        code_value = normalize_code(unquote(candidate))
        if not code_value:
            raise ValidationError({'value': 'Enter or scan a label code.'})
        workspace = get_current_workspace()
        code = LabelCode.objects.select_related('identity', 'identity__target_content_type').filter(workspace=workspace, code=code_value).first()
        if code:
            return Response(_resolution(code, workspace))
        if LabelCode.objects.filter(code=code_value).exists():
            return Response({'status': 'wrong_workspace', 'message': 'This code belongs to another workspace.'})
        return Response({'status': 'unknown', 'message': 'No label uses this code.'})


router = routers.SimpleRouter()
router.register(r'identities', LabelIdentityViewSet, basename='label-identity')
router.register(r'templates', LabelTemplateViewSet, basename='label-template')
router.register(r'print-jobs', LabelPrintJobViewSet, basename='label-print-job')
router.register(r'codes', LabelCodeViewSet, basename='label-code')
