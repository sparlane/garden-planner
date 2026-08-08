"""Read the production-cost subledger, and repost it from corrected facts.

Every write here drives a service in `services`; nothing decides anything on its
own. The reads are derivations over the stored layers, so a report and the
ledger can never disagree.

Money is serialized as decimal strings with a currency code, never as floats,
and every figure says whether it is provisional or final. A batch is wholly one
or the other, so exactly one of the two totals carries a number and the other is
null — a caller cannot add them together by accident because there is never
anything in both.
"""

# The action serializers here are payloads rather than writable resources, so
# none implements DRF's `create` or `update`; `ActionSerializer` refuses both
# once on their behalf.
# pylint: disable=duplicate-code,abstract-method

from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_nested import routers

from inventory.rest_query import parse_integer
from plantings.models import ProductionBatch, SpecificPlant
from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .models import CostAllocation, CostAllocationRun
from .services import (
    batch_cost_breakdown,
    plant_cost_breakdown,
    recalculate_batch_costs,
)


def _model_errors(error):
    """Translate a Django validation error into DRF's field-error shape."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


def _run_domain_action(function, *args, **kwargs):
    """Run a domain service, surfacing its errors as DRF field errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """Base for payloads that drive a service rather than a model."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class RecalculateSerializer(ActionSerializer):
    """A required explanation for reposting a batch's allocations."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class CostAllocationSerializer(serializers.ModelSerializer):
    """One immutable layer of cost, exactly as it was posted."""

    class Meta:
        model = CostAllocation
        fields = [
            'pk',
            'run',
            'batch',
            'source_type',
            'application_line',
            'sowing_posting',
            'generation_residual',
            'movement',
            'target_type',
            'seed_tray_cell',
            'seed_tray_generation',
            'specific_plant',
            'basis',
            'basis_weight',
            'base_quantity',
            'base_unit',
            'unit_cost',
            'amount',
            'currency_code',
            'reversal_of',
            'created',
        ]
        read_only_fields = fields


class CostAllocationRunSerializer(serializers.ModelSerializer):
    """One recalculation, and what it had to change."""

    class Meta:
        model = CostAllocationRun
        fields = [
            'pk',
            'batch',
            'trigger',
            'reason',
            'posted_count',
            'reversed_count',
            'froze_output',
            'created_by',
            'created',
        ]
        read_only_fields = fields


class CostAllocationViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Every layer this workspace has posted, including the reversed ones.

    Reversals and reversed layers are both listed by default. They are the audit
    trail; hiding them would leave a report unable to show why a figure changed.
    Pass `effective=true` for the layers that still count.
    """

    queryset = CostAllocation.objects.select_related('run').order_by('pk')
    serializer_class = CostAllocationSerializer

    def get_queryset(self):
        """Filter layers by batch, plant, run, and whether they still count."""
        queryset = super().get_queryset()
        query = self.request.query_params
        for parameter, field in (
            ('batch', 'batch_id'),
            ('plant', 'specific_plant_id'),
            ('run', 'run_id'),
            ('cell', 'seed_tray_cell_id'),
        ):
            value = parse_integer(query.get(parameter), parameter)
            if value is not None:
                queryset = queryset.filter(**{field: value})
        if 'target_type' in query:
            queryset = queryset.filter(target_type=query['target_type'])
        if query.get('effective') == 'true':
            queryset = queryset.filter(reversal_of__isnull=True, reversal__isnull=True)
        return queryset


class CostAllocationRunViewSet(CurrentWorkspaceViewSetMixin, viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Why each layer exists: the event that caused it to be written."""

    queryset = CostAllocationRun.objects.order_by('created', 'pk')
    serializer_class = CostAllocationRunSerializer

    def get_queryset(self):
        """Filter runs by the batch they recalculated."""
        queryset = super().get_queryset()
        batch = parse_integer(self.request.query_params.get('batch'), 'batch')
        if batch is not None:
            queryset = queryset.filter(batch_id=batch)
        return queryset


class BatchCostViewSet(CurrentWorkspaceViewSetMixin, viewsets.GenericViewSet):
    """Where one batch's input cost went, and what it has not reached."""

    queryset = ProductionBatch.objects.select_related('workspace').order_by('pk')
    serializer_class = CostAllocationSerializer

    def retrieve(self, request, pk=None):  # pylint: disable=unused-argument
        """Report the batch's sources, allocations, buckets, and totals."""
        return Response(
            _run_domain_action(batch_cost_breakdown, self.get_object()),
        )

    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):  # pylint: disable=unused-argument
        """Repost this batch's allocations from corrected source facts.

        Never edits an amount: a layer that no longer matches is reversed and
        its replacement posted beside it. On a finalized batch this is
        append-only, because those frozen layers are the point of finalizing —
        reopening the batch is the audited way to undo them.
        """
        values = RecalculateSerializer(data=request.data)
        values.is_valid(raise_exception=True)
        batch = self.get_object()
        run = _run_domain_action(
            recalculate_batch_costs,
            batch,
            request.user,
            values.validated_data['reason'],
        )
        batch.refresh_from_db()
        return Response({
            'run': None if run is None else CostAllocationRunSerializer(run).data,
            'breakdown': batch_cost_breakdown(batch),
        })


class PlantCostViewSet(CurrentWorkspaceViewSetMixin, viewsets.GenericViewSet):
    """What one seedling cost, from which inputs, and where its value went."""

    queryset = SpecificPlant.objects.select_related(
        'cell_planting__seed_tray_planting__batch',
    ).order_by('pk')
    serializer_class = CostAllocationSerializer

    def retrieve(self, request, pk=None):  # pylint: disable=unused-argument
        """Report one plant's layers, its value, and its disposition."""
        return Response(
            _run_domain_action(plant_cost_breakdown, self.get_object()),
        )


router = routers.SimpleRouter()
router.register(r'batches', BatchCostViewSet, basename='batchcost')
router.register(r'plants', PlantCostViewSet, basename='plantcost')
router.register(r'allocations', CostAllocationViewSet, basename='costallocation')
router.register(r'runs', CostAllocationRunViewSet, basename='costallocationrun')
