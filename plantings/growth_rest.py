"""Nursery growth catalogs and append-only observation endpoints."""

# DRF's declarative serializers and inherited viewsets intentionally use tiny
# classes and framework-specified method signatures.
# pylint: disable=too-many-ancestors,missing-class-docstring,missing-function-docstring,duplicate-code,unused-argument

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from attachments.rest import AttachmentSerializer
from common.catalog import CatalogViewSetMixin
from common.coded import StableCodeSerializerMixin, normalize_code
from common.retirement import RetirementSerializerMixin
from workspaces.models import Workspace
from workspaces.models import get_current_workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .growth import correct_observation, record_observation
from .models import GrowthStage, NurseryObservation, PlantGrade


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class CatalogSerializer(
    StableCodeSerializerMixin, RetirementSerializerMixin, serializers.ModelSerializer,
):
    """Shared validation for stable workspace-owned nursery catalogs.

    The code rule and the activation rule are both the catalog's rather than
    this app's, so both come from ``common``. What is left here is running the
    model's own validation against the workspace the record belongs to, which
    is what turns a second setting under a code somebody already used into a
    field error rather than a database one.
    """

    def validate(self, attrs):
        """Validate the setting this payload would save."""
        attrs = super().validate(attrs)
        workspace = self.instance.workspace if self.instance else get_current_workspace()
        taken = self._taken_code_errors(workspace, attrs)
        if taken:
            raise serializers.ValidationError(taken)
        candidate = self.instance or self.Meta.model(  # pylint: disable=no-member
            workspace=workspace,
        )
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return attrs

    def _taken_code_errors(self, workspace, attrs):
        """Refuse a code another setting in this workspace already holds.

        The unique constraint refuses it too, but as an error about the whole
        record rather than about the field somebody typed. It is worth saying
        properly, because a code already taken is not a resemblance to warn
        about: it is exactly the record the operator is looking for, and the
        answer is to merge into that one rather than to add a second.
        """
        code = attrs.get('code')
        if code is None:
            return {}
        model = self.Meta.model  # pylint: disable=no-member
        taken = model.objects.filter(workspace=workspace, code=normalize_code(code))
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        holder = taken.first()
        if holder is None:
            return {}
        return {'code': (
            f'{holder} already uses the code {holder.code}. Merge into it '
            f'rather than adding a second.'
        )}


class GrowthStageSerializer(CatalogSerializer):
    class Meta:
        model = GrowthStage
        fields = [
            'pk', 'code', 'name', 'display_order', 'active', 'merged_into',
            'target_days',
        ]


class PlantGradeSerializer(CatalogSerializer):
    class Meta:
        model = PlantGrade
        fields = ['pk', 'code', 'name', 'display_order', 'active', 'merged_into']


class CatalogViewSet(  # pylint: disable=too-many-ancestors
    CatalogViewSetMixin,
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):
    required_workspace_modes = (Workspace.Mode.NURSERY,)
    http_method_names = ['get', 'post', 'patch', 'head', 'options']


class GrowthStageViewSet(CatalogViewSet):
    queryset = GrowthStage.objects.all()
    serializer_class = GrowthStageSerializer


class PlantGradeViewSet(CatalogViewSet):
    queryset = PlantGrade.objects.all()
    serializer_class = PlantGradeSerializer


class NurseryObservationSerializer(serializers.ModelSerializer):
    plants = serializers.SerializerMethodField()
    cohort = serializers.SerializerMethodField()
    stage_name = serializers.CharField(source='stage.name', read_only=True, allow_null=True)
    grade_name = serializers.CharField(source='grade.name', read_only=True, allow_null=True)
    attachments = AttachmentSerializer(source='image_attachments', many=True, read_only=True)

    class Meta:
        model = NurseryObservation
        fields = [
            'pk', 'plants', 'cohort', 'stage', 'stage_name', 'grade', 'grade_name',
            'container_item', 'container_count', 'container_name',
            'container_size_label', 'container_volume_ml', 'container_footprint_m2',
            'height_cm', 'spread_cm', 'root_condition', 'expected_ready',
            'photo_url', 'occurred_at', 'notes', 'corrects', 'created_by', 'created',
            'input_application',
            'attachments',
        ]

    def get_plants(self, observation):
        return list(observation.targets.exclude(plant=None).values_list('plant_id', flat=True))

    def get_cohort(self, observation):
        return observation.targets.exclude(cohort=None).values_list('cohort_id', flat=True).first()


class ObservationWriteSerializer(
    CurrentWorkspaceSerializerMixin, serializers.Serializer,
):  # pylint: disable=abstract-method
    plants = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=False,
    )
    cohort = serializers.IntegerField(min_value=1, required=False)
    stage = serializers.PrimaryKeyRelatedField(queryset=GrowthStage.objects.all(), required=False, allow_null=True)
    grade = serializers.PrimaryKeyRelatedField(queryset=PlantGrade.objects.all(), required=False, allow_null=True)
    container_item = serializers.PrimaryKeyRelatedField(
        queryset=NurseryObservation._meta.get_field('container_item').remote_field.model.objects.all(),
        required=False, allow_null=True,
    )
    container_count = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    height_cm = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True)
    spread_cm = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True)
    root_condition = serializers.CharField(max_length=255, required=False, allow_blank=True)
    expected_ready = serializers.DateField(required=False, allow_null=True)
    photo_url = serializers.URLField(required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    # Scoping the three pickers to the workspace is what the shared mixin does
    # everywhere else, and it is also what keeps a retired stage or grade from
    # being observed onto a plant: retirement takes a record out of the
    # selectors from beside the workspace filter rather than from each
    # serializer that names one.
    workspace_field_lookups = {
        'stage': 'workspace', 'grade': 'workspace', 'container_item': 'workspace',
    }


class NurseryObservationViewSet(
    RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet,
):
    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = NurseryObservation.objects.select_related('stage', 'grade', 'container_item', 'created_by').prefetch_related('targets', 'image_attachments')
    serializer_class = NurseryObservationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('plant'):
            queryset = queryset.filter(targets__plant_id=self.request.query_params['plant'])
        if self.request.query_params.get('cohort'):
            queryset = queryset.filter(targets__cohort_id=self.request.query_params['cohort'])
        return queryset.distinct()

    def create(self, request):
        serializer = ObservationWriteSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        plants = values.pop('plants', ())
        cohort = values.pop('cohort', None)
        try:
            observation = record_observation(
                self.get_current_workspace(), request.user,
                plant_ids=plants, cohort_id=cohort, **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(NurseryObservationSerializer(observation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def correct(self, request, pk=None):
        serializer = ObservationWriteSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        values.pop('plants', None)
        values.pop('cohort', None)
        try:
            observation = correct_observation(
                self.get_current_workspace(), request.user,
                observation_id=self.get_object().pk, **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(NurseryObservationSerializer(observation).data, status=status.HTTP_201_CREATED)


def register_growth_routes(router):
    router.register(r'growth-stages', GrowthStageViewSet)
    router.register(r'plant-grades', PlantGradeViewSet)
    router.register(r'nursery-observations', NurseryObservationViewSet)
