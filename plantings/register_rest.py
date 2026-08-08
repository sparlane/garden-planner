"""The nursery plant register endpoint.

The page and its totals come back from one request built from one validated
filter set, so the counts an operator reads always describe the list underneath
them. This is the only paginated collection in the project; task 04 owns the
global contract change, and this endpoint carries its own pagination class so
that change does not have to arrive first.
"""

# pylint: disable=duplicate-code

from datetime import timedelta

from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils import timezone

from workspaces.models import Workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin, RequireWorkspaceModeMixin

from .register import (
    parse_register_filters,
    register_queryset,
    register_totals,
)


#: The largest selection the register will resolve to explicit plant IDs. A
#: bigger answer than this is a filter the operator has not finished writing.
MAX_SELECTION = 5000


class NurseryRegisterPagination(PageNumberPagination):
    """Page the register and report the whole selection's counts alongside.

    The totals are computed from the queryset before it is sliced, so paging
    never narrows them — an operator reading "412 available" on page 3 is
    reading the filter, not the page.
    """

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200

    def __init__(self):
        super().__init__()
        self.totals = {}

    def paginate_queryset(self, queryset, request, view=None):
        """Count the whole selection, then return the requested page of it."""
        self.totals = register_totals(queryset)
        return super().paginate_queryset(queryset, request, view=view)

    def get_paginated_response(self, data):
        """Return the page and the counts it was drawn from together."""
        response = super().get_paginated_response(data)
        response.data['totals'] = self.totals
        return response


class NurseryRegisterSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One register row: a projection, not the plant's editable record.

    Deliberately flat and shallow where `SpecificPlantSerializer` is nested —
    a register row needs where a plant is now, not everywhere it has been.
    """

    pk = serializers.IntegerField(read_only=True)
    batch = serializers.IntegerField(
        source='cell_planting.seed_tray_planting.batch_id',
        read_only=True,
    )
    batch_code = serializers.CharField(read_only=True)
    variety = serializers.IntegerField(
        source='cell_planting.seed_tray_planting.batch.variety_id',
        read_only=True,
    )
    variety_name = serializers.CharField(read_only=True)
    plant_name = serializers.CharField(read_only=True)
    germinated = serializers.DateTimeField(read_only=True)
    age_days = serializers.SerializerMethodField()
    lifecycle_state = serializers.CharField(read_only=True)
    sellable = serializers.BooleanField(read_only=True)
    final_outcome = serializers.CharField(read_only=True, allow_null=True)
    final_outcome_at = serializers.DateTimeField(read_only=True, allow_null=True)
    location_type = serializers.CharField(
        source='current_location_type',
        read_only=True,
        allow_null=True,
    )
    location_label = serializers.CharField(source='current_location_label', read_only=True)
    seed_tray = serializers.IntegerField(source='current_seed_tray', read_only=True, allow_null=True)
    seed_tray_cell = serializers.IntegerField(
        source='current_seed_tray_cell',
        read_only=True,
        allow_null=True,
    )
    garden_square = serializers.IntegerField(
        source='current_garden_square',
        read_only=True,
        allow_null=True,
    )
    located_since = serializers.DateTimeField(read_only=True, allow_null=True)
    expected_ready_early = serializers.SerializerMethodField()
    expected_ready_late = serializers.SerializerMethodField()
    cost = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True, allow_null=True)
    currency_code = serializers.SerializerMethodField()

    def get_age_days(self, plant):
        """Report how long this plant has been growing, in whole days."""
        return (timezone.now() - plant.germinated).days

    def get_expected_ready_early(self, plant):
        """Project the earliest ready date from the crop's maturity range.

        A projection from catalog data, not a recorded readiness date; task 54
        adds the observed one, and until then this is display only and neither
        filterable nor sortable.
        """
        return self._expected_ready(plant, 'maturity_days_min')

    def get_expected_ready_late(self, plant):
        """Project the latest ready date from the crop's maturity range."""
        return self._expected_ready(plant, 'maturity_days_max')

    def get_currency_code(self, plant):  # pylint: disable=unused-argument
        """Name the currency the cost is expressed in, so it cannot separate."""
        return self.context['workspace'].currency_code

    @staticmethod
    def _expected_ready(plant, field):
        """Offset germination by the variety's days, falling back to the crop."""
        variety = plant.cell_planting.seed_tray_planting.batch.variety
        days = getattr(variety, field) or getattr(variety.plant, field)
        if days is None:
            return None
        return plant.germinated + timedelta(days=days)


class NurseryPlantRegisterViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):  # pylint: disable=too-many-ancestors
    """Search current plants as operational nursery inventory.

    A projection over the source records rather than a second plant inventory,
    so a row links to the plant's own detail route for lineage, history, and
    cost instead of restating them here.
    """

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    serializer_class = NurseryRegisterSerializer
    pagination_class = NurseryRegisterPagination

    def get_queryset(self):
        """Select the plants the request's validated filters describe."""
        return register_queryset(
            self.get_current_workspace(),
            parse_register_filters(self.request.query_params),
        )

    def get_serializer_context(self):
        """Give rows the workspace their money and measurements belong to."""
        context = super().get_serializer_context()
        context['workspace'] = self.get_current_workspace()
        return context

    @action(detail=False, url_path='ids')
    def ids(self, request):  # pylint: disable=unused-argument
        """Resolve the current filters to the plant IDs they select.

        Bulk actions act on a filter, not on a page. Returning the IDs at the
        moment of the action — rather than freezing them when the operator
        first ticked "everything matching" — means a selection describes what
        is true now instead of what was on screen several edits ago.
        """
        queryset = self.filter_queryset(self.get_queryset())
        plants = list(queryset.values_list('pk', flat=True)[:MAX_SELECTION + 1])
        if len(plants) > MAX_SELECTION:
            raise ValidationError({
                'detail': (
                    f'That selection is larger than {MAX_SELECTION} plants. '
                    'Narrow the filters before acting on it.'
                ),
            })
        return Response({'count': len(plants), 'plants': plants})


def register_register_routes(router):
    """Register the nursery plant register with the plantings router."""
    router.register(r'register', NurseryPlantRegisterViewSet, basename='plant-register')
