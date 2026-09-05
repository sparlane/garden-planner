"""Nursery production assumptions, demand, plan actions, and projections."""

# DRF viewsets and serializers use framework-defined small method signatures.
# pylint: disable=too-many-ancestors,missing-class-docstring,missing-function-docstring,duplicate-code,unused-argument

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from workspaces.models import Workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
    RequireWorkspaceModeMixin,
)

from .assumption_variance import (
    assumption_variance_rows,
    revise_assumption,
    revision_draft,
)
from .models import (
    NurseryPlanDemand,
    NurseryPlanInputRequirement,
    NurseryPlanIssue,
    NurseryPlanMilestone,
    NurseryPlanRequirement,
    NurseryPlanningAssumption,
    NurseryPlanningInputAssumption,
    NurseryPlanningStageAssumption,
    NurseryProductionPlan,
)
from .planning import approve_plan, calculate_plan, plan_variance, revise_plan


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class PlanningStageAssumptionSerializer(
        CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)

    class Meta:
        model = NurseryPlanningStageAssumption
        fields = [
            'pk', 'assumption', 'stage', 'stage_name', 'sequence', 'lead_days',
            'loss_rate', 'location', 'location_name', 'capacity_basis',
            'capacity_per_plant',
        ]

    workspace_field_lookups = {
        'assumption': 'workspace',
        'stage': 'workspace',
        'location': 'workspace',
    }


class PlanningInputAssumptionSerializer(
        CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    base_unit = serializers.CharField(source='item.base_unit', read_only=True)

    class Meta:
        model = NurseryPlanningInputAssumption
        fields = [
            'pk', 'assumption', 'item', 'item_name', 'quantity_per_plant', 'base_unit',
        ]

    workspace_field_lookups = {
        'assumption': 'workspace',
        'item': 'workspace',
    }


class PlanningAssumptionSerializer(
        CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    stages = PlanningStageAssumptionSerializer(many=True, read_only=True)
    inputs = PlanningInputAssumptionSerializer(many=True, read_only=True)

    class Meta:
        model = NurseryPlanningAssumption
        fields = [
            'pk', 'variety', 'variety_name', 'effective_from', 'effective_until',
            'germination_rate', 'seeds_per_cluster', 'tray_density', 'notes',
            'stages', 'inputs', 'created',
        ]

    workspace_field_lookups = {'variety': 'workspace'}


class ReviseStageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One stage's accepted duration and loss for the next version."""

    stage = serializers.IntegerField(min_value=1)
    lead_days = serializers.IntegerField(min_value=0, required=False)
    loss_rate = serializers.DecimalField(
        max_digits=7, decimal_places=6, min_value=0, max_value=Decimal('0.999999'),
        required=False,
    )


class ReviseAssumptionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The figures an operator accepted, which are the only ones written.

    Every value is optional because a revision may accept the observed
    germination rate and leave the tray density alone; anything omitted is
    carried across from the version being replaced rather than defaulted.
    """

    effective_from = serializers.DateField()
    germination_rate = serializers.DecimalField(
        max_digits=7, decimal_places=6, min_value=Decimal('0.000001'), max_value=1,
        required=False,
    )
    seeds_per_cluster = serializers.IntegerField(min_value=1, required=False)
    tray_density = serializers.IntegerField(min_value=1, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    stages = ReviseStageSerializer(many=True, required=False, default=list)


class PlanMilestoneSerializer(serializers.ModelSerializer):
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)

    class Meta:
        model = NurseryPlanMilestone
        fields = [
            'pk', 'stage', 'stage_name', 'sequence', 'planned_date',
            'input_quantity', 'expected_output', 'location', 'location_name',
            'capacity_basis', 'capacity_required',
        ]


class PlanInputRequirementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    base_unit = serializers.CharField(source='item.base_unit', read_only=True)

    class Meta:
        model = NurseryPlanInputRequirement
        fields = ['pk', 'item', 'item_name', 'quantity', 'base_unit']


class PlanRequirementSerializer(serializers.ModelSerializer):
    milestones = PlanMilestoneSerializer(many=True, read_only=True)
    inputs = PlanInputRequirementSerializer(many=True, read_only=True)

    class Meta:
        model = NurseryPlanRequirement
        fields = [
            'pk', 'assumption', 'required_seeds', 'required_clusters',
            'required_trays', 'expected_finished', 'sowing_date',
            'expected_ready_from', 'expected_ready_until', 'assumption_snapshot',
            'batch', 'milestones', 'inputs',
        ]


class PlanDemandSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    requirement = PlanRequirementSerializer(read_only=True)

    class Meta:
        model = NurseryPlanDemand
        fields = [
            'pk', 'plan', 'variety', 'variety_name', 'product_reference',
            'target_quantity', 'ready_from', 'ready_until', 'source', 'priority',
            'customer_reference', 'order_reference', 'source_line_reference',
            'notes', 'requirement',
        ]

    workspace_field_lookups = {'plan': 'workspace', 'variety': 'workspace'}

    def validate(self, attrs):
        plan = attrs.get('plan', self.instance.plan if self.instance else None)
        if plan and plan.status == NurseryProductionPlan.Status.APPROVED:
            raise serializers.ValidationError({'plan': 'Approved plan demand is immutable.'})
        return attrs


class PlanIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = NurseryPlanIssue
        fields = [
            'pk', 'demand', 'kind', 'message', 'required_quantity',
            'available_quantity',
        ]


class ProductionPlanSerializer(serializers.ModelSerializer):
    demand_lines = PlanDemandSerializer(many=True, read_only=True)
    issues = PlanIssueSerializer(many=True, read_only=True)

    class Meta:
        model = NurseryProductionPlan
        fields = [
            'pk', 'code', 'version', 'status', 'direction', 'sowing_date',
            'supersedes', 'notes', 'approved_at', 'approved_by', 'created_by',
            'created', 'updated', 'demand_lines', 'issues',
        ]
        read_only_fields = [
            'version', 'status', 'supersedes', 'approved_at', 'approved_by',
            'created_by', 'created', 'updated',
        ]

    def validate(self, attrs):
        if self.instance and self.instance.status == NurseryProductionPlan.Status.APPROVED:
            raise serializers.ValidationError({'status': 'Approved plans are immutable.'})
        return attrs


class ImportDemandSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The delivery window whose confirmed commitments a plan takes on."""

    ready_from = serializers.DateField()
    ready_until = serializers.DateField()


class NurseryPlanningViewSetMixin(
        RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin):
    required_workspace_modes = (Workspace.Mode.NURSERY,)
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']


class PlanningAssumptionViewSet(
        NurseryPlanningViewSetMixin, viewsets.ModelViewSet):
    """Read, write, and close the feedback loop on planning assumptions.

    The variance actions live here rather than beside the reports because the
    comparison belongs at the moment of the decision: an operator editing an
    assumption should see the last observed figure next to the one they are
    about to keep.
    """

    queryset = NurseryPlanningAssumption.objects.select_related('variety').prefetch_related(
        'stages__stage', 'stages__location', 'inputs__item',
    )
    serializer_class = PlanningAssumptionSerializer

    @action(detail=False)
    def variance(self, request):
        """Compare every version with the batches sown under it, in one pass.

        A list action rather than a field on each assumption: the comparison
        reads the whole workspace's batches either way, and computing it per
        serialized row would run that work once per version on screen.
        """
        return Response(assumption_variance_rows(self.get_current_workspace()))

    @action(detail=True, url_path='revision-draft')
    def revision_draft(self, request, pk=None):
        """Pre-fill the next version with what happened, saving nothing."""
        return Response(revision_draft(self.get_object()))

    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        """Write the next version from the figures an operator accepted."""
        values = ReviseAssumptionSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        try:
            revision = revise_assumption(self.get_object(), **values.validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(
            self.get_serializer(revision).data, status=status.HTTP_201_CREATED,
        )


class PlanningStageAssumptionViewSet(
        NurseryPlanningViewSetMixin, viewsets.ModelViewSet):
    queryset = NurseryPlanningStageAssumption.objects.select_related(
        'assumption', 'stage', 'location',
    )
    serializer_class = PlanningStageAssumptionSerializer
    workspace_lookup = 'assumption__workspace'
    bind_workspace_on_create = False


class PlanningInputAssumptionViewSet(
        NurseryPlanningViewSetMixin, viewsets.ModelViewSet):
    queryset = NurseryPlanningInputAssumption.objects.select_related('assumption', 'item')
    serializer_class = PlanningInputAssumptionSerializer
    workspace_lookup = 'assumption__workspace'
    bind_workspace_on_create = False


class PlanDemandViewSet(NurseryPlanningViewSetMixin, viewsets.ModelViewSet):
    queryset = NurseryPlanDemand.objects.select_related('plan', 'variety').prefetch_related(
        'requirement__milestones__stage', 'requirement__inputs__item',
    )
    serializer_class = PlanDemandSerializer
    workspace_lookup = 'plan__workspace'
    bind_workspace_on_create = False

    def destroy(self, request, *args, **kwargs):
        if self.get_object().plan.status == NurseryProductionPlan.Status.APPROVED:
            return Response(
                {'plan': ['Approved plan demand is immutable.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class ProductionPlanViewSet(NurseryPlanningViewSetMixin, viewsets.ModelViewSet):
    queryset = NurseryProductionPlan.objects.select_related(
        'supersedes', 'approved_by', 'created_by',
    ).prefetch_related(
        'demand_lines__variety',
        'demand_lines__requirement__milestones__stage',
        'demand_lines__requirement__inputs__item',
        'issues',
    )
    serializer_class = ProductionPlanSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def perform_create(self, serializer):
        serializer.save(
            workspace=self.get_current_workspace(),
            created_by=self.request.user,
        )

    def _run(self, operation):
        try:
            result = operation(self.get_object())
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return result

    @action(detail=True, methods=['post'])
    def calculate(self, request, pk=None):
        self._run(calculate_plan)
        plan = self.get_queryset().get(pk=self.get_object().pk)
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        plan = self._run(lambda current: approve_plan(current, request.user))
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        revision = self._run(lambda current: revise_plan(current, request.user))
        return Response(self.get_serializer(revision).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='import-demand')
    def import_demand(self, request, pk=None):
        """Read confirmed orders falling due in a window in as plan demand.

        `sales` is imported here rather than at the top of the module for the
        reason `cohort_availability` defers its own reach: the nursery is built
        without knowledge of who is buying from it, and only this endpoint
        needs the other direction.
        """
        from sales.demand import import_committed_demand  # pylint: disable=import-outside-toplevel

        values = ImportDemandSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        window = values.validated_data
        self._run(lambda current: import_committed_demand(
            current, window['ready_from'], window['ready_until'],
        ))
        plan = self.get_queryset().get(pk=self.get_object().pk)
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['get'])
    def variance(self, request, pk=None):
        return Response(plan_variance(self.get_object()))


def register_planning_routes(router):
    router.register(r'planning-assumptions', PlanningAssumptionViewSet)
    router.register(r'planning-stage-assumptions', PlanningStageAssumptionViewSet)
    router.register(r'planning-input-assumptions', PlanningInputAssumptionViewSet)
    router.register(r'production-plan-demand', PlanDemandViewSet)
    router.register(r'production-plans', ProductionPlanViewSet)
