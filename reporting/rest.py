"""Read-only HTTP views for centralized Nursery reports."""

from rest_framework.views import APIView

from workspaces.models import Workspace
from workspaces.scoping import RequireWorkspaceModeMixin

from .common import csv_response, normalized_filters, report_response
from .filters import (
    CommerceFilters,
    DashboardFilters,
    InventoryBalanceFilters,
    MovementFilters,
    OrderFilters,
    ProductionFilters,
    SerializedTrayFilters,
    StocktakeVarianceFilters,
    TraceFilters,
)
from .commerce import dashboard_report, order_report, profitability_report
from .production import production_batches
from .traceability import lot_trace, plant_trace
from .inventory import (
    inventory_balances,
    movement_history,
    serialized_trays,
    stocktake_variances,
)


class ReportView(RequireWorkspaceModeMixin, APIView):  # pylint: disable=not-callable
    """Validate a report's filters and render JSON or its matching export."""

    required_workspace_modes = (Workspace.Mode.NURSERY,)
    http_method_names = ['get', 'head', 'options']
    filter_class = None
    builder = None
    export = False

    def get(self, request):  # pylint: disable=not-callable
        """Render one validated report as paginated JSON or CSV."""
        serializer = self.filter_class(  # pylint: disable=not-callable
            data=request.query_params,
        )
        serializer.is_valid(raise_exception=True)
        filters = normalized_filters(serializer)
        report = self.builder(  # pylint: disable=not-callable
            self._workspace(), filters,
        )
        if self.export:
            return csv_response(report)
        return report_response(request, report)

    @staticmethod
    def _workspace():
        from workspaces.models import get_current_workspace  # pylint: disable=import-outside-toplevel
        return get_current_workspace()


def _view(name, filters, builder, export=False):
    """Create one small configured view class without duplicating dispatch code."""
    return type(name, (ReportView,), {
        'filter_class': filters,
        'builder': staticmethod(builder),
        'export': export,
    })


InventoryBalanceView = _view(
    'InventoryBalanceView', InventoryBalanceFilters, inventory_balances,
)
InventoryBalanceExportView = _view(
    'InventoryBalanceExportView', InventoryBalanceFilters, inventory_balances, True,
)
SerializedTrayView = _view(
    'SerializedTrayView', SerializedTrayFilters, serialized_trays,
)
SerializedTrayExportView = _view(
    'SerializedTrayExportView', SerializedTrayFilters, serialized_trays, True,
)
MovementView = _view('MovementView', MovementFilters, movement_history)
MovementExportView = _view(
    'MovementExportView', MovementFilters, movement_history, True,
)
StocktakeVarianceView = _view(
    'StocktakeVarianceView', StocktakeVarianceFilters, stocktake_variances,
)
StocktakeVarianceExportView = _view(
    'StocktakeVarianceExportView', StocktakeVarianceFilters,
    stocktake_variances, True,
)
ProductionView = _view(
    'ProductionView', ProductionFilters, production_batches,
)
ProductionExportView = _view(
    'ProductionExportView', ProductionFilters, production_batches, True,
)


class TraceView(ReportView):  # pylint: disable=arguments-differ
    """Bind an exact plant or lot URL identity into the trace service."""

    filter_class = TraceFilters
    trace_builder = None

    def get(self, request, identity):  # pylint: disable=not-callable,arguments-differ
        """Render one exact identity's bidirectional trace."""
        serializer = self.filter_class(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        filters = normalized_filters(serializer)
        report = self.trace_builder(  # pylint: disable=not-callable
            self._workspace(), identity, filters,
        )
        if self.export:
            return csv_response(report)
        return report_response(request, report)


def _trace_view(name, builder, export=False):
    return type(name, (TraceView,), {
        'trace_builder': staticmethod(builder), 'export': export,
    })


PlantTraceView = _trace_view('PlantTraceView', plant_trace)
PlantTraceExportView = _trace_view('PlantTraceExportView', plant_trace, True)
LotTraceView = _trace_view('LotTraceView', lot_trace)
LotTraceExportView = _trace_view('LotTraceExportView', lot_trace, True)

OrderView = _view('OrderView', OrderFilters, order_report)
OrderExportView = _view('OrderExportView', OrderFilters, order_report, True)
ProfitabilityView = _view(
    'ProfitabilityView', CommerceFilters, profitability_report,
)
ProfitabilityExportView = _view(
    'ProfitabilityExportView', CommerceFilters, profitability_report, True,
)
DashboardView = _view(
    'DashboardView', DashboardFilters, dashboard_report,
)
