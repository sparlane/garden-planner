"""Derived lot/location balance and valuation REST resource."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import NamedTuple

from django.db.models import Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from locations.models import Location, location_full_name
from workspaces.models import get_current_workspace

from .ledger import MONEY_QUANTUM, physical_balances
from .models import InventoryUnit, StockLot
from .rest_query import parse_boolean, parse_date, parse_integer


class DerivedBalances(NamedTuple):
    """The lookups every row is built against, gathered in one pass each.

    `physical` is the whole request's balances, keyed by lot and location, so
    that a row costs a dictionary read rather than an aggregate. `names` is
    the whole catalog's pk-to-name map, which is what lets a row name its
    location in full without a query per ancestor.
    """

    physical: dict
    item_totals: dict
    numbered: dict
    promised: dict
    names: dict


class BalanceFilters(NamedTuple):
    """Parsed filters for one balance request."""

    item: int | None
    lot: int | None
    location: int | None
    expires_before: object
    low_stock: bool | None


def _parse_filters(query_params):
    """Return one immutable collection of validated balance filters."""
    return BalanceFilters(
        item=parse_integer(query_params.get('item'), 'item'),
        lot=parse_integer(query_params.get('lot'), 'lot'),
        location=parse_integer(query_params.get('location'), 'location'),
        expires_before=parse_date(
            query_params.get('expires_before'),
            'expires_before',
        ),
        low_stock=parse_boolean(
            query_params.get('low_stock'),
            'low_stock',
        ),
    )


class BalanceView(APIView):
    """Return current derived lot/location balances and valuation."""

    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        """Filter lot rows without changing item-wide low-stock totals."""
        workspace = get_current_workspace()
        filters = _parse_filters(request.query_params)
        all_lots = StockLot.objects.select_related('item').filter(
            workspace=workspace,
        )
        if filters.item is not None:
            all_lots = all_lots.filter(item_id=filters.item)
        item_total_lots = list(all_lots)
        result_lots = all_lots
        if filters.lot is not None:
            result_lots = result_lots.filter(pk=filters.lot)
        if filters.expires_before:
            result_lots = result_lots.filter(
                expires_on__lte=filters.expires_before,
            )
        result_lots = list(result_lots)
        # Balanced across every lot the item totals need, which is a superset
        # of the rows the filters keep, so one grouped query serves both and
        # each row's quantity is derived exactly once per request.
        balances = physical_balances(item_total_lots)
        locations = self._history_locations(workspace, balances)
        derived = DerivedBalances(
            physical=balances,
            item_totals=self._item_totals(item_total_lots, locations, balances),
            numbered=self._numbered_counts(workspace, result_lots),
            promised=self._promised_counts(workspace, result_lots),
            names=dict(
                Location.objects.filter(workspace=workspace).values_list('pk', 'name'),
            ),
        )
        return Response(self._rows(result_lots, locations, filters, derived))

    @staticmethod
    def _numbered_counts(workspace, lots):
        """Count numbered units per lot and location in one query.

        Deliberately not a count per row: this endpoint answers in a fixed
        number of queries however many rows it returns, and a count per row
        would put that back for every item, numbered or not.
        """
        counts = defaultdict(int)
        rows = InventoryUnit.objects.filter(
            workspace=workspace,
            source_lot_id__in=[lot.pk for lot in lots],
            current_location__isnull=False,
            active=True,
        ).values_list('source_lot_id', 'current_location_id')
        for lot_id, location_id in rows:
            counts[(lot_id, location_id)] += 1
        return counts

    @staticmethod
    def _promised_counts(workspace, lots):
        """Total the counted sales reservations held per lot and location.

        Gathered in one query for the reason `_numbered_counts` gathers its
        own that way. Only reserved draws count: a pending one is a selection
        somebody is still drafting, which warns rather than holds.
        """
        from sales.models import SalesOrderAllocation  # pylint: disable=import-outside-toplevel

        promised = defaultdict(Decimal)
        rows = SalesOrderAllocation.objects.filter(
            stock_lot__workspace=workspace,
            stock_lot_id__in=[lot.pk for lot in lots],
            status=SalesOrderAllocation.Status.RESERVED,
        ).values('stock_lot_id', 'source_location_id').annotate(total=Sum('quantity'))
        for row in rows:
            promised[(row['stock_lot_id'], row['source_location_id'])] = Decimal(row['total'])
        return promised

    @staticmethod
    def _history_locations(workspace, balances):
        """Return each lot's distinct locations across its complete history.

        Read off the balances rather than the ledger a second time: a lot has
        a balance at exactly the places its movements touched, so the keys
        already are the history, and asking again would re-read every
        movement row to learn what the first pass knew.
        """
        location_ids = defaultdict(set)
        for lot_id, location_id in balances:
            location_ids[lot_id].add(location_id)
        locations = {
            location.pk: location
            for location in Location.objects.filter(
                workspace=workspace,
                pk__in={pk for values in location_ids.values() for pk in values},
            )
        }
        return {
            lot_id: [locations[pk] for pk in sorted(ids)]
            for lot_id, ids in location_ids.items()
        }

    @staticmethod
    def _item_totals(lots, locations, balances):
        """Aggregate availability across every lot/location for low-stock state."""
        totals = defaultdict(Decimal)
        for lot in lots:
            for location in locations.get(lot.pk, []):
                totals[lot.item_id] += balances[(lot.pk, location.pk)]
        return totals

    @classmethod
    def _rows(cls, lots, locations, filters, derived):
        """Build filtered serialized rows from already-derived relationships."""
        rows = []
        for lot in lots:
            for location in locations.get(lot.pk, []):
                if filters.location is not None and location.pk != filters.location:
                    continue
                is_low = cls._is_low(lot, derived.item_totals)
                if filters.low_stock is not None and is_low != filters.low_stock:
                    continue
                rows.append(cls._balance_row(lot, location, is_low, derived))
        return rows

    @staticmethod
    def _is_low(lot, item_totals):
        """Compare an item's total available quantity with its catalog threshold."""
        threshold = lot.item.reorder_level
        return threshold is not None and item_totals[lot.item_id] <= threshold

    @staticmethod
    def _balance_row(lot, location, is_low, derived):
        """Serialize one lot/location quantity and immutable-cost valuation."""
        physical = derived.physical[(lot.pk, location.pk)]
        promised = derived.promised[(lot.pk, location.pk)]
        numbered_quantity = Decimal(derived.numbered[(lot.pk, location.pk)])
        bulk = physical - numbered_quantity
        value = None
        if lot.base_unit_cost is not None:
            value = (physical * lot.base_unit_cost).quantize(
                MONEY_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        return {
            'lot': lot.pk,
            'lot_identifier': lot.identifier,
            'item': lot.item_id,
            'item_name': lot.item.name,
            'location': location.pk,
            'location_name': location.name,
            'location_full_name': location_full_name(location, derived.names),
            'physical_quantity': f'{physical:.9f}',
            'bulk_quantity': f'{bulk:.9f}',
            'numbered_quantity': f'{numbered_quantity:.9f}',
            'reserved_quantity': f'{promised:.9f}',
            # What a counted sale may still draw on: loose stock that nothing
            # has already been promised out of. A numbered pot is standing here
            # in its own right and is sold as itself, so it is not part of it.
            'unpromised_quantity': f'{bulk - promised:.9f}',
            'available_quantity': f'{physical - promised:.9f}',
            'base_unit': lot.item.base_unit,
            'base_unit_cost': (
                f'{lot.base_unit_cost:.12f}'
                if lot.base_unit_cost is not None
                else None
            ),
            'valuation': f'{value:.4f}' if value is not None else None,
            'currency_code': lot.currency_code,
            'expires_on': lot.expires_on,
            'low_stock': is_low,
        }
