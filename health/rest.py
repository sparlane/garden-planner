"""REST contracts for reviewed nursery health observations."""

# DRF's declarative fields and inherited viewsets intentionally use compact
# framework-shaped classes.
# pylint: disable=too-many-ancestors,missing-class-docstring,missing-function-docstring
# pylint: disable=abstract-method,unused-argument,duplicate-code

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from workspaces.models import Workspace, get_current_workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin, RequireWorkspaceModeMixin

from .models import (
    HealthDiagnosis,
    HealthObservation,
    HealthObservationDiagnosis,
    HealthObservationType,
)
from .services import correct_observation, preview_observation, record_observation


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class CatalogSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        candidate = self.instance or self.Meta.model(  # pylint: disable=no-member
            workspace=get_current_workspace(),
        )
        if self.instance and 'code' in attrs and attrs['code'] != self.instance.code:
            raise serializers.ValidationError({'code': 'Stable catalog codes cannot be changed.'})
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return attrs


class HealthObservationTypeSerializer(CatalogSerializer):
    class Meta:
        model = HealthObservationType
        fields = ['pk', 'code', 'name', 'display_order', 'active']


class HealthDiagnosisSerializer(CatalogSerializer):
    class Meta:
        model = HealthDiagnosis
        fields = ['pk', 'code', 'name', 'category', 'display_order', 'active']


class CatalogViewSet(
    RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet,
):
    required_workspace_modes = (Workspace.Mode.NURSERY,)
    http_method_names = ['get', 'post', 'patch', 'head', 'options']


class HealthObservationTypeViewSet(CatalogViewSet):
    queryset = HealthObservationType.objects.all()
    serializer_class = HealthObservationTypeSerializer


class HealthDiagnosisViewSet(CatalogViewSet):
    queryset = HealthDiagnosis.objects.all()
    serializer_class = HealthDiagnosisSerializer


class ScopeSerializer(serializers.Serializer):
    type = serializers.ChoiceField(
        choices=['plant', 'cohort', 'tray', 'generation', 'batch', 'location'],
    )
    id = serializers.IntegerField(min_value=1)


class DiagnosisAssessmentSerializer(serializers.Serializer):
    diagnosis = serializers.PrimaryKeyRelatedField(queryset=HealthDiagnosis.objects.all())
    certainty = serializers.ChoiceField(choices=HealthObservationDiagnosis.Certainty.choices)


class EvidenceSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    label = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class ObservationWriteSerializer(serializers.Serializer):
    scopes = ScopeSerializer(many=True, allow_empty=False)
    reviewed_digest = serializers.CharField(max_length=64)
    observation_type = serializers.PrimaryKeyRelatedField(
        queryset=HealthObservationType.objects.all(),
    )
    severity = serializers.ChoiceField(choices=HealthObservation.Severity.choices)
    diagnoses = DiagnosisAssessmentSerializer(many=True, required=False)
    evidence = EvidenceSerializer(many=True, required=False)
    occurred_at = serializers.DateTimeField(required=False)
    follow_up_due_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        workspace = get_current_workspace()
        if attrs['observation_type'].workspace_id != workspace.pk:
            raise serializers.ValidationError({
                'observation_type': 'Choose a value from this workspace.',
            })
        for item in attrs.get('diagnoses', ()):
            if item['diagnosis'].workspace_id != workspace.pk:
                raise serializers.ValidationError({
                    'diagnoses': 'Choose diagnoses from this workspace.',
                })
        return attrs


class CorrectionWriteSerializer(serializers.Serializer):
    observation_type = serializers.PrimaryKeyRelatedField(
        queryset=HealthObservationType.objects.all(),
    )
    severity = serializers.ChoiceField(choices=HealthObservation.Severity.choices)
    diagnoses = DiagnosisAssessmentSerializer(many=True, required=False)
    evidence = EvidenceSerializer(many=True, required=False)
    occurred_at = serializers.DateTimeField(required=False)
    follow_up_due_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    correction_reason = serializers.CharField(allow_blank=False)

    def validate(self, attrs):
        workspace = get_current_workspace()
        if attrs['observation_type'].workspace_id != workspace.pk:
            raise serializers.ValidationError({
                'observation_type': 'Choose a value from this workspace.',
            })
        for item in attrs.get('diagnoses', ()):
            if item['diagnosis'].workspace_id != workspace.pk:
                raise serializers.ValidationError({
                    'diagnoses': 'Choose diagnoses from this workspace.',
                })
        return attrs


class HealthObservationSerializer(serializers.ModelSerializer):
    observation_type_name = serializers.CharField(source='observation_type.name', read_only=True)
    scopes = serializers.SerializerMethodField()
    affected = serializers.SerializerMethodField()
    diagnoses = serializers.SerializerMethodField()
    evidence = serializers.SerializerMethodField()
    affected_count = serializers.SerializerMethodField()

    class Meta:
        model = HealthObservation
        fields = [
            'pk', 'observation_type', 'observation_type_name', 'severity',
            'occurred_at', 'follow_up_due_at', 'notes', 'scopes', 'affected',
            'affected_count', 'diagnoses', 'evidence', 'corrects',
            'correction_reason', 'created_by', 'created',
        ]

    def get_scopes(self, observation):
        return [
            {'type': row.target_type, 'id': row.target.pk, 'label': row.label}
            for row in observation.scopes.all()
        ]

    def get_affected(self, observation):
        return [
            {
                'type': 'plant' if row.plant_id else 'cohort',
                'id': row.plant_id or row.cohort_id,
                'quantity': row.quantity,
            }
            for row in observation.affected_stock.all()
        ]

    def get_affected_count(self, observation):
        return sum(row.quantity for row in observation.affected_stock.all())

    def get_diagnoses(self, observation):
        return [
            {
                'diagnosis': row.diagnosis_id,
                'name': row.diagnosis.name,
                'category': row.diagnosis.category,
                'certainty': row.certainty,
            }
            for row in observation.diagnoses.all()
        ]

    def get_evidence(self, observation):
        return [
            {'url': row.url, 'label': row.label}
            for row in observation.evidence_links.all()
        ]


class HealthObservationViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = HealthObservation.objects.select_related(
        'observation_type', 'created_by',
    ).prefetch_related(
        'scopes__plant', 'scopes__cohort', 'scopes__generation',
        'scopes__batch', 'scopes__location', 'affected_stock',
        'diagnoses__diagnosis', 'evidence_links',
    )
    serializer_class = HealthObservationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get('plant'):
            queryset = queryset.filter(affected_stock__plant_id=params['plant'])
        if params.get('cohort'):
            queryset = queryset.filter(affected_stock__cohort_id=params['cohort'])
        if params.get('severity'):
            queryset = queryset.filter(severity=params['severity'])
        if params.get('diagnosis'):
            queryset = queryset.filter(diagnoses__diagnosis_id=params['diagnosis'])
        return queryset.filter(correction__isnull=True).distinct()

    @action(detail=False, methods=['post'])
    def preview(self, request):
        serializer = ScopeSerializer(data=request.data.get('scopes'), many=True)
        serializer.is_valid(raise_exception=True)
        try:
            payload = preview_observation(
                self.get_current_workspace(), serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(payload)

    def create(self, request):
        serializer = ObservationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        diagnoses = [
            (item['diagnosis'], item['certainty'])
            for item in values.pop('diagnoses', ())
        ]
        try:
            observation = record_observation(
                self.get_current_workspace(), request.user,
                diagnoses=diagnoses, evidence=values.pop('evidence', ()), **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(
            HealthObservationSerializer(observation).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def correct(self, request, pk=None):
        serializer = CorrectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        diagnoses = [
            (item['diagnosis'], item['certainty'])
            for item in values.pop('diagnoses', ())
        ]
        try:
            observation = correct_observation(
                self.get_current_workspace(), request.user, self.get_object(),
                diagnoses=diagnoses, evidence=values.pop('evidence', ()), **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(
            HealthObservationSerializer(observation).data,
            status=status.HTTP_201_CREATED,
        )


router = routers.SimpleRouter()
router.register(r'observation-types', HealthObservationTypeViewSet)
router.register(r'diagnoses', HealthDiagnosisViewSet)
router.register(r'observations', HealthObservationViewSet)
