"""Read-only HTTP views for centralized Nursery reports."""

from rest_framework.views import APIView

from workspaces.models import Workspace
from workspaces.scoping import RequireWorkspaceModeMixin

from .common import csv_response, normalized_filters, report_response
from .filters import (
    InventoryBalanceFilters,
    MovementFilters,
    SerializedTrayFilters,
    StocktakeVarianceFilters,
)
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
