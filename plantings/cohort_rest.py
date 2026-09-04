"""Nursery cohort register and transactional operation endpoints."""

# DRF calls detail actions with a `pk` keyword even when `get_object()` resolves
# it, so the required method signatures intentionally retain that argument.
# Serializer method names are prescribed by DRF and repeat their field names.
# pylint: disable=unused-argument,missing-function-docstring,too-many-branches

from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import DateField, DateTimeField, DurationField, ExpressionWrapper, F, IntegerField, OuterRef, Subquery, Sum, TextField, Value
from django.db.models.functions import Now
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
from costing.models import CostAllocation
from health.availability import with_quarantine

from . import cohort_availability
from .cohorts import (
    change_cohort,
    merge_cohorts,
    observe_cohort,
    promote_cohort,
    split_cohort,
)
from .growth import current_growth
from .loss import RECORDABLE_CAUSES
from .models import CohortEvent, CohortOperation, NurseryObservation, PlantCohort
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
    loss_cause = serializers.CharField(source='operation.loss_cause', read_only=True)

    class Meta:
        model = CohortEvent
        fields = [
            'pk', 'action', 'occurred_at', 'reason', 'loss_cause',
            'quantity_before', 'quantity_delta', 'quantity_after',
            'state_before', 'state_after', 'location_before', 'location_after',
            'source_cohorts', 'created',
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
    stage = serializers.SerializerMethodField()
    stage_name = serializers.SerializerMethodField()
    grade = serializers.SerializerMethodField()
    grade_name = serializers.SerializerMethodField()
    container = serializers.SerializerMethodField()
    container_name = serializers.SerializerMethodField()
    container_size = serializers.SerializerMethodField()
    container_count = serializers.SerializerMethodField()
    expected_ready = serializers.SerializerMethodField()
    quarantined = serializers.BooleanField(read_only=True)
    reserved_quantity = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()

    class Meta:
        model = PlantCohort
        fields = [
            'pk', 'batch', 'batch_code', 'variety', 'variety_name', 'plant_name',
            'source_sowing', 'quantity', 'lifecycle_state', 'location',
            'location_name', 'observed_at', 'revision', 'notes', 'label_code',
            'cost', 'currency_code',
            'stage', 'stage_name', 'grade', 'grade_name', 'container',
            'container_name', 'container_size', 'container_count', 'expected_ready',
            'quarantined', 'reserved_quantity', 'available_quantity',
            'created', 'updated',
        ]
        read_only_fields = [
            'quantity', 'lifecycle_state', 'observed_at', 'revision', 'created', 'updated',
        ]

    def get_reserved_quantity(self, cohort):
        """Return how much of the count a live sales reservation holds.

        Read from the register's own annotation where there is one, so a page
        of fifty rows costs one subquery rather than fifty; a cohort handed
        straight back by a domain command carries none, and answers for itself.
        """
        if hasattr(cohort, 'reserved_quantity'):
            return cohort.reserved_quantity
        return cohort_availability.reserved_quantity(cohort)

    def get_available_quantity(self, cohort):
        """Return the part of the count nothing has been promised out of."""
        if hasattr(cohort, 'available_quantity'):
            return cohort.available_quantity
        return cohort_availability.available_quantity(cohort)

    def get_label_code(self, cohort):
        """Return the active physical identity issued for this cohort."""
        from labels.services import ensure_identity  # pylint: disable=import-outside-toplevel

        return ensure_identity(cohort).codes.get(status='active').code

    def get_cost(self, cohort):
        """Sum what the stock still standing here cost, unknown preserved.

        Layers for quantities already sold out of the block share this column
        and are left out: they are cost of sale, and adding them would say the
        plants on the bench are worth what the ones that left cost too.
        """
        rows = cohort.cost_allocations.filter(
            target_type=CostAllocation.TargetType.PLANT_COHORT,
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        if rows.filter(amount__isnull=True).exists():
            return None
        return rows.aggregate(total=Sum('amount'))['total']

    def get_stage(self, cohort):
        if hasattr(cohort, 'current_stage'):
            return cohort.current_stage
        stage = self._growth(cohort)['stage']
        return stage.pk if stage else None

    def get_stage_name(self, cohort):
        if hasattr(cohort, 'current_stage_name'):
            return cohort.current_stage_name
        stage = self._growth(cohort)['stage']
        return stage.name if stage else None

    def get_grade(self, cohort):
        if hasattr(cohort, 'current_grade'):
            return cohort.current_grade
        grade = self._growth(cohort)['grade']
        return grade.pk if grade else None

    def get_grade_name(self, cohort):
        if hasattr(cohort, 'current_grade_name'):
            return cohort.current_grade_name
        grade = self._growth(cohort)['grade']
        return grade.name if grade else None

    def get_container(self, cohort):
        if hasattr(cohort, 'current_container'):
            return cohort.current_container
        item = self._growth(cohort)['container_item']
        return item.pk if item else None

    def get_container_name(self, cohort):
        if hasattr(cohort, 'current_container_name'):
            return cohort.current_container_name or None
        return self._growth(cohort)['container_name'] or None

    def get_container_size(self, cohort):
        if hasattr(cohort, 'current_container_size'):
            return cohort.current_container_size or None
        return self._growth(cohort)['container_size_label'] or None

    def get_container_count(self, cohort):
        if hasattr(cohort, 'current_container_count'):
            return cohort.current_container_count
        return self._growth(cohort)['container_count']

    def get_expected_ready(self, cohort):
        if hasattr(cohort, 'current_expected_ready'):
            return cohort.current_expected_ready
        return self._growth(cohort)['expected_ready']

    @staticmethod
    def _growth(cohort):
        """Replay only service-returned rows that do not carry list annotations."""
        if not hasattr(cohort, '_serialized_growth'):
            cohort._serialized_growth = current_growth(cohort)  # pylint: disable=protected-access
        return cohort._serialized_growth  # pylint: disable=protected-access


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
    container_count = serializers.IntegerField(required=False, min_value=1)
    location = serializers.PrimaryKeyRelatedField(
        queryset=PlantCohort._meta.get_field('location').remote_field.model.objects.all(),
        required=False,
        allow_null=True,
    )
    loss_cause = serializers.ChoiceField(choices=RECORDABLE_CAUSES, required=False)
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

    @staticmethod
    def _observation(field, output_field=None, observed_field=None):
        return Subquery(
            NurseryObservation.objects.filter(
                targets__cohort=OuterRef('pk'), correction__isnull=True,
            ).exclude(
                **{f'{observed_field or field}__isnull': True},
            ).order_by('-occurred_at', '-pk').values(field)[:1],
            output_field=output_field,
        )

    def get_serializer_class(self):
        return CohortDetailSerializer if self.action == 'retrieve' else PlantCohortSerializer

    def get_queryset(self):
        queryset = cohort_availability.with_availability(
            with_quarantine(super().get_queryset(), 'cohort'),
        ).annotate(
            current_stage=self._observation('stage_id', IntegerField()),
            current_stage_name=self._observation('stage__name', TextField()),
            current_stage_days=self._observation('stage__target_days', IntegerField(), 'stage'),
            current_stage_at=self._observation('occurred_at', DateTimeField(), 'stage'),
            current_grade=self._observation('grade_id', IntegerField()),
            current_grade_name=self._observation('grade__name', TextField()),
            current_container=self._observation('container_item_id', IntegerField()),
            current_container_name=self._observation('container_name', TextField(), 'container_item'),
            current_container_size=self._observation('container_size_label', TextField(), 'container_item'),
            current_container_count=self._observation('container_count', IntegerField(), 'container_item'),
            current_expected_ready=self._observation('expected_ready', DateField()),
        ).annotate(
            stage_due_at=ExpressionWrapper(
                F('current_stage_at') + ExpressionWrapper(
                    F('current_stage_days') * Value(timedelta(days=1)),
                    output_field=DurationField(),
                ),
                output_field=DateTimeField(),
            ),
        )
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
        for name in ('stage', 'grade', 'container'):
            if params.get(name):
                queryset = queryset.filter(**{f'current_{name}': params[name]})
        if params.get('expected_ready_from'):
            queryset = queryset.filter(current_expected_ready__gte=params['expected_ready_from'])
        if params.get('expected_ready_to'):
            queryset = queryset.filter(current_expected_ready__lte=params['expected_ready_to'])
        if params.get('stage_overdue') == 'true':
            queryset = queryset.filter(stage_due_at__lt=Now())
        if params.get('active') == 'true':
            queryset = queryset.filter(quantity__gt=0)
        if params.get('quarantined') in {'true', 'false'}:
            queryset = queryset.filter(quarantined=params['quarantined'] == 'true')
        if params.get('loss_cause'):
            queryset = queryset.filter(
                events__operation__action=CohortOperation.Action.LOSS,
                events__operation__loss_cause=params['loss_cause'],
            ).distinct()
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
            quarantined=False,
        )
        params = request.query_params.copy()
        params.setlist('state', ['available'])
        individuals = register_queryset(
            self.get_current_workspace(), parse_register_filters(params),
        )
        cohort_quantity = cohorts.aggregate(total=Sum('quantity'))['total'] or 0
        cohort_reserved = cohorts.aggregate(total=Sum('reserved_quantity'))['total'] or 0
        individual_count = individuals.count()
        return Response({
            'cohort_quantity': cohort_quantity,
            # What is left once live reservations are taken off. Reported
            # beside the gross figure rather than instead of it: a grower
            # counting benches wants the stock that is standing there, and a
            # salesperson quoting wants the part of it still free to promise.
            'cohort_reserved_quantity': cohort_reserved,
            'cohort_unpromised_quantity': cohort_quantity - cohort_reserved,
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
        values.pop('container_count', None)
        try:
            changed, _operation_row = change_cohort(
                self.get_current_workspace(), request.user,
                cohort_id=cohort.pk,
                action=operation_action,
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
        values.pop('loss_cause', None)
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
        values.pop('loss_cause', None)
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
