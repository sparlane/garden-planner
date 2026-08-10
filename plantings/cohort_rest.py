"""Nursery cohort register and transactional operation endpoints."""

# DRF calls detail actions with a `pk` keyword even when `get_object()` resolves
# it, so the required method signatures intentionally retain that argument.
# pylint: disable=unused-argument

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .cohorts import (
    change_cohort,
    merge_cohorts,
    observe_cohort,
    promote_cohort,
    split_cohort,
)
from .models import CohortEvent, CohortOperation, PlantCohort
from .register import parse_register_filters, register_queryset


class CohortPagination(PageNumberPagination):
    """Bound cohort register pages while totals continue to describe the filter."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def _errors(error):
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


class CohortEventSerializer(serializers.ModelSerializer):
    """Expose immutable quantity, state, location, and lineage history."""

    source_cohorts = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    action = serializers.CharField(source='operation.action', read_only=True)
    occurred_at = serializers.DateTimeField(source='operation.occurred_at', read_only=True)
    reason = serializers.CharField(source='operation.reason', read_only=True)

    class Meta:
        model = CohortEvent
        fields = [
            'pk', 'action', 'occurred_at', 'reason', 'quantity_before',
            'quantity_delta', 'quantity_after', 'state_before', 'state_after',
            'location_before', 'location_after', 'source_cohorts', 'created',
        ]


class PlantCohortSerializer(serializers.ModelSerializer):
    """Current cohort register row with batch-derived crop identity."""

    batch_code = serializers.CharField(source='batch.code', read_only=True)
    variety = serializers.IntegerField(source='batch.variety_id', read_only=True)
    variety_name = serializers.CharField(source='batch.variety.name', read_only=True)
    plant_name = serializers.CharField(source='batch.variety.plant.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)
    label_code = serializers.SerializerMethodField()
    cost = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source='workspace.currency_code', read_only=True)

    class Meta:
        model = PlantCohort
        fields = [
            'pk', 'batch', 'batch_code', 'variety', 'variety_name', 'plant_name',
            'source_sowing', 'quantity', 'lifecycle_state', 'location',
            'location_name', 'observed_at', 'revision', 'notes', 'label_code',
            'cost', 'currency_code',
            'created', 'updated',
        ]
        read_only_fields = [
            'quantity', 'lifecycle_state', 'observed_at', 'revision', 'created', 'updated',
        ]

    def get_label_code(self, cohort):
        """Return the active physical identity issued for this cohort."""
        from labels.services import ensure_identity  # pylint: disable=import-outside-toplevel

        return ensure_identity(cohort).codes.get(status='active').code

    def get_cost(self, cohort):
        """Sum effective cohort layers while preserving unknown as unknown."""
        rows = cohort.cost_allocations.filter(
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        if rows.filter(amount__isnull=True).exists():
            return None
        return rows.aggregate(total=Sum('amount'))['total']


class CohortDetailSerializer(PlantCohortSerializer):
    """Add the cohort's append-only history and promoted plant IDs."""

    events = CohortEventSerializer(many=True, read_only=True)
    promoted_plants = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta(PlantCohortSerializer.Meta):
        fields = PlantCohortSerializer.Meta.fields + ['events', 'promoted_plants']


class ObserveCohortSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):  # pylint: disable=abstract-method
    """Validate one initial cohort observation."""

    batch = serializers.PrimaryKeyRelatedField(queryset=PlantCohort._meta.get_field('batch').remote_field.model.objects.all())
    source_sowing = serializers.PrimaryKeyRelatedField(
        queryset=PlantCohort._meta.get_field('source_sowing').remote_field.model.objects.all(),
        required=False,
        allow_null=True,
    )
    quantity = serializers.IntegerField(min_value=1)
    location = serializers.PrimaryKeyRelatedField(
        queryset=PlantCohort._meta.get_field('location').remote_field.model.objects.all(),
        required=False,
        allow_null=True,
    )
    occurred_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    idempotency_key = serializers.UUIDField()
    workspace_field_lookups = {
        'batch': 'workspace',
        'source_sowing': 'workspace',
        'location': 'workspace',
    }


class CohortActionSerializer(
    CurrentWorkspaceSerializerMixin,
    serializers.Serializer,
):  # pylint: disable=abstract-method
    """Shared request envelope for a confirmed cohort command."""

    expected_revision = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.UUIDField()
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    quantity = serializers.IntegerField(required=False, min_value=0)
    location = serializers.PrimaryKeyRelatedField(
        queryset=PlantCohort._meta.get_field('location').remote_field.model.objects.all(),
        required=False,
        allow_null=True,
    )
    disposition = serializers.ChoiceField(
        choices=('failed', 'culled', 'donated', 'other'),
        required=False,
    )
    workspace_field_lookups = {'location': 'workspace'}


class MergeCohortSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate a compatible many-to-one merge request."""

    target = serializers.IntegerField(min_value=1)
    sources = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    revisions = serializers.DictField(child=serializers.IntegerField(min_value=1))
    idempotency_key = serializers.UUIDField()
    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=False)


class PlantCohortViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Search cohorts and route all writes through audited domain commands."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    queryset = PlantCohort.objects.select_related(
        'batch__variety__plant', 'source_sowing', 'location',
    ).prefetch_related('events__operation', 'events__source_cohorts', 'promoted_plants')
    pagination_class = CohortPagination

    def get_serializer_class(self):
        return CohortDetailSerializer if self.action == 'retrieve' else PlantCohortSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        for name in ('batch', 'location', 'source_sowing'):
            if params.get(name):
                if name == 'location':
                    location_model = PlantCohort._meta.get_field('location').remote_field.model
                    path = location_model.objects.filter(
                        workspace=self.get_current_workspace(), pk=params[name],
                    ).values_list('path', flat=True).first()
                    queryset = (
                        queryset.filter(location__path__startswith=path)
                        if path else queryset.none()
                    )
                else:
                    queryset = queryset.filter(**{f'{name}_id': params[name]})
        if params.get('variety'):
            queryset = queryset.filter(batch__variety_id=params['variety'])
        if params.get('state'):
            queryset = queryset.filter(lifecycle_state=params['state'])
        if params.get('active') == 'true':
            queryset = queryset.filter(quantity__gt=0)
        if params.get('search'):
            search = params['search'].strip()
            queryset = queryset.filter(batch__code__icontains=search)
        return queryset

    def list(self, request, *args, **kwargs):
        """Return one page plus quantity totals for the complete filter."""
        response = super().list(request, *args, **kwargs)
        queryset = self.filter_queryset(self.get_queryset()).order_by()
        totals = {
            'cohort_count': queryset.count(),
            'quantity': queryset.aggregate(total=Sum('quantity'))['total'] or 0,
        }
        for state_name, _label in PlantCohort.LifecycleState.choices:
            totals[state_name] = (
                queryset.filter(lifecycle_state=state_name).aggregate(total=Sum('quantity'))['total'] or 0
            )
        response.data['cohort_totals'] = totals
        return response

    @action(detail=False, methods=['get'])
    def availability(self, request):
        """Report anonymous and identified available stock under shared filters."""
        cohorts = self.get_queryset().filter(
            lifecycle_state=PlantCohort.LifecycleState.AVAILABLE,
            quantity__gt=0,
        )
        params = request.query_params.copy()
        params.setlist('state', ['available'])
        individuals = register_queryset(
            self.get_current_workspace(), parse_register_filters(params),
        )
        cohort_quantity = cohorts.aggregate(total=Sum('quantity'))['total'] or 0
        individual_count = individuals.count()
        return Response({
            'cohort_quantity': cohort_quantity,
            'individual_count': individual_count,
            'combined_total': cohort_quantity + individual_count,
        })

    @action(detail=False, methods=['post'])
    def observe(self, request):
        """Record an initial anonymous quantity through the domain service."""
        serializer = ObserveCohortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            cohort, _operation_row = observe_cohort(
                self.get_current_workspace(), request.user, **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(CohortDetailSerializer(cohort).data, status=status.HTTP_201_CREATED)

    def _change(self, request, cohort, operation_action):
        """Validate and dispatch one single-cohort state or quantity command."""
        serializer = CohortActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        disposition = values.pop('disposition', None)
        try:
            changed, _operation_row = change_cohort(
                self.get_current_workspace(), request.user,
                cohort_id=cohort.pk,
                action=operation_action,
                payload_extra={'disposition': disposition} if disposition else None,
                **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(CohortDetailSerializer(changed).data)

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        """Reconcile the cohort to a physical count."""
        return self._change(request, self.get_object(), CohortOperation.Action.ADJUST)

    @action(detail=True, methods=['post'])
    def loss(self, request, pk=None):
        """Remove an explained lost quantity from the cohort."""
        return self._change(request, self.get_object(), CohortOperation.Action.LOSS)

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """Move the complete cohort to a capacity-checked location."""
        return self._change(request, self.get_object(), CohortOperation.Action.MOVE)

    @action(detail=True, methods=['post'])
    def ready(self, request, pk=None):
        """Make a growing cohort commercially available."""
        return self._change(request, self.get_object(), CohortOperation.Action.READY)

    @action(detail=True, methods=['post'])
    def retain(self, request, pk=None):
        """Remove a growing or available cohort from sale stock."""
        return self._change(request, self.get_object(), CohortOperation.Action.RETAIN)

    @action(detail=True, methods=['post'])
    def split(self, request, pk=None):
        """Create a separately identified child quantity."""
        serializer = CohortActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        values.pop('disposition', None)
        try:
            child, _operation_row = split_cohort(
                self.get_current_workspace(), request.user,
                cohort_id=self.get_object().pk, **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(CohortDetailSerializer(child).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def promote(self, request, pk=None):
        """Replace part of a cohort with concrete plant identities."""
        serializer = CohortActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        values.pop('location', None)
        values.pop('disposition', None)
        try:
            plants, operation = promote_cohort(
                self.get_current_workspace(), request.user,
                cohort_id=self.get_object().pk, **values,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response({'operation': operation.pk, 'plants': [plant.pk for plant in plants]})

    @action(detail=False, methods=['post'])
    def merge(self, request):
        """Fold compatible source cohorts into the selected target."""
        serializer = MergeCohortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            cohort, _operation_row = merge_cohorts(
                self.get_current_workspace(), request.user, **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(CohortDetailSerializer(cohort).data)


def register_cohort_routes(router):
    """Attach the cohort inventory contract to the plantings API."""
    router.register(r'cohorts', PlantCohortViewSet, basename='plant-cohorts')
