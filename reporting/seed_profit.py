"""Profit per seed lot: what it has earned, what is promised, and what is left.

A sale is never recorded against a seed lot. It is recorded against a plant or
a cohort, which belongs to a batch, and the batch was sown from one or more
lots. So the report follows that chain and shares each batch's results over
the lots it was sown from, in proportion to the seed each one supplied.

Three figures answer the question, each over a different slice of the stock:

* realized: dispatched sales less what they cost and what was lost, over all
  time. This is the existing profitability calculation, grouped by batch.
  A cost that is still provisional (its batch has not finalized its output) is
  counted, and the row is flagged `provisional` because it can still move.
* committed: units promised on open orders and not yet dispatched, at the
  order's own price. Revenue only, because their cost is not final until they
  leave.
* pending: surviving units that are neither sold nor promised, at their share
  of the batch's held plant cost. It is what is still to be sold.
"""

# Each figure is carried per batch and per lot, so the locals are many.
# pylint: disable=too-many-locals

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from costing.models import CostAllocation
from inventory.models import StockLot
from sales.models import SalesOrderAllocation

from .commerce import profitability_report
from .common import NOT_CONSOLIDATED, Report, decimal_string
from .production import production_batches

ZERO = Decimal('0')
OPEN_STATUSES = (
    SalesOrderAllocation.Status.PENDING,
    SalesOrderAllocation.Status.RESERVED,
)
COLUMNS = (
    'lot_id', 'lot_identifier', 'item_name', 'variety_id', 'variety_name',
    'batches', 'seed_quantity', 'currency_code', 'net_sales', 'direct_cogs',
    'production_loss', 'realized_profit', 'committed_units',
    'committed_revenue', 'pending_units', 'pending_cost', 'provisional', 'complete',
)


def _lot_weights(workspace):
    """Return ({batch: {lot: share}}, {batch: {lot: seed quantity}}).

    A batch sown from nothing recorded (a direct sow with no stock posting)
    has no lot to answer to; the caller shares it wholly to `None`, which shows
    as the row for sales with no seed lot recorded.
    """
    rows = CostAllocation.objects.filter(
        workspace=workspace,
        sowing_posting__isnull=False,
        reversal_of__isnull=True,
        reversal__isnull=True,
    ).values('batch_id', 'sowing_posting__movement__lot_id').annotate(
        quantity=Sum('base_quantity'),
    )
    quantities = defaultdict(dict)
    for row in rows:
        lot_id = row['sowing_posting__movement__lot_id']
        quantities[row['batch_id']][lot_id] = abs(row['quantity'] or ZERO)
    weights = {}
    for batch_id, lots in quantities.items():
        total = sum(lots.values(), ZERO)
        even = Decimal(1) / len(lots)
        weights[batch_id] = {
            lot: (qty / total if total else even) for lot, qty in lots.items()
        }
    return weights, quantities


def _realized(workspace, today):
    """Return per-batch sales, cost and loss over all time, from the P&L rows.

    Also the batches with an unvalued row, and those with a provisional one.
    """
    report = profitability_report(workspace, {
        'date_from': '1970-01-01', 'date_to': today,
    })
    batches = defaultdict(lambda: defaultdict(Decimal))
    codes = defaultdict(set)
    incomplete = set()
    provisional = set()
    for row in report.rows:
        batch_id = row['batch_id']
        if batch_id is None:
            continue
        if row['unvalued']:
            incomplete.add(batch_id)
            continue
        if row['provisional']:
            provisional.add(batch_id)
        values = batches[batch_id]
        values['net_sales'] += Decimal(row['net_sales'])
        values['direct_cogs'] += sum(
            (Decimal(row[field]) for field in (
                'plant_cogs', 'tray_cogs', 'packaging_cogs', 'other_cogs',
            )), ZERO,
        )
        values['production_loss'] += Decimal(row['production_loss'])
        codes[batch_id].update((row['currency_code'], row['cogs_currency_code']))
    return batches, codes, incomplete, provisional


def _committed(workspace):
    """Return per-batch units and revenue promised on orders still open."""
    allocations = SalesOrderAllocation.objects.filter(
        Q(plant__isnull=False) | Q(plant_cohort__isnull=False),
        line__order__workspace=workspace, status__in=OPEN_STATUSES,
    ).select_related('line__order', 'plant', 'plant_cohort')
    committed = defaultdict(lambda: {'units': ZERO, 'revenue': ZERO, 'codes': set()})
    for allocation in allocations:
        target = allocation.plant or allocation.plant_cohort
        line = allocation.line
        entry = committed[target.batch_id]
        entry['units'] += allocation.quantity
        entry['revenue'] += line.subtotal_ex_tax * allocation.quantity / line.quantity
        entry['codes'].add(line.order.currency_code)
    return committed


def _batch_pending(batch_row, committed_units):
    """Return (units, cost) still unsold and unpromised for one batch.

    The cost is None when the batch cannot state its held cost, which keeps an
    unknown figure out of the sum rather than counting it as zero.
    """
    current = Decimal(batch_row['current_seedlings'])
    units = max(ZERO, current - committed_units)
    held = batch_row['plant_inventory_value']
    if not units:
        return units, ZERO
    if held is None or not current:
        return units, None
    return units, Decimal(held) * units / current


def _empty_lot(variety_name):
    return {
        'batches': 0, 'seed_quantity': ZERO, 'codes': set(), 'incomplete': False,
        'net_sales': ZERO, 'direct_cogs': ZERO, 'production_loss': ZERO,
        'committed_units': ZERO, 'committed_revenue': ZERO, 'provisional': False,
        'pending_units': ZERO, 'pending_cost': ZERO, 'variety_name': variety_name,
    }


def seed_profit(workspace, filters):
    """Report realized, committed and pending money per seed lot."""
    today = timezone.now().date().isoformat()
    batch_rows = {
        row['batch_id']: row for row in production_batches(
            workspace, {k: v for k, v in filters.items() if k == 'variety'},
        ).rows
    }
    weights, quantities = _lot_weights(workspace)
    realized, realized_codes, unfinished, provisional = _realized(workspace, today)
    committed = _committed(workspace)
    lots = {}
    for batch_id, batch in batch_rows.items():
        promised = committed.get(batch_id) or {'units': ZERO, 'revenue': ZERO, 'codes': set()}
        pending_units, pending_cost = _batch_pending(batch, promised['units'])
        codes = realized_codes[batch_id] | promised['codes']
        codes.add(batch['currency_code'] or workspace.currency_code)
        incomplete = (
            batch_id in unfinished or len(codes) != 1 or pending_cost is None
        )
        for lot_id, share in (weights.get(batch_id) or {None: Decimal(1)}).items():
            entry = lots.setdefault(
                (lot_id, batch['variety_id']), _empty_lot(batch['variety_name']),
            )
            entry['batches'] += 1
            entry['seed_quantity'] += quantities.get(batch_id, {}).get(lot_id, ZERO)
            entry['codes'] |= codes
            entry['incomplete'] = entry['incomplete'] or incomplete
            entry['provisional'] = entry['provisional'] or batch_id in provisional
            for field in ('net_sales', 'direct_cogs', 'production_loss'):
                entry[field] += realized[batch_id][field] * share
            entry['committed_units'] += promised['units'] * share
            entry['committed_revenue'] += promised['revenue'] * share
            entry['pending_units'] += pending_units * share
            entry['pending_cost'] += (pending_cost or ZERO) * share
    rows = _rows(workspace, lots)
    rows.sort(key=lambda row: (row['variety_name'], row['lot_id'] or 0))
    withheld = sum(not row['complete'] for row in rows)
    provisional_rows = sum(row['provisional'] for row in rows)
    return Report(
        name='seed-profit', filters=filters, rows=rows, columns=COLUMNS,
        not_stated_columns=('realized_profit', 'pending_cost', 'currency_code'),
        not_stated='not stated: cost incomplete or currencies mixed',
        totals={'lots': len(rows), 'varieties': _variety_totals(rows)},
        reconciliation={
            'profit_equation': 'realized profit = net sales - direct COGS - production loss',
            'sharing': (
                "A batch's results are shared over the seed lots it was sown "
                'from in proportion to the seed each supplied. Packaging, which '
                'belongs to no batch, is in the profitability report but not here.'
            ),
            'committed': 'units promised on open orders, at the order price excluding tax',
            'pending': 'surviving units neither sold nor promised, at their share of held plant cost',
        },
        data_quality=([{
            'code': 'withheld_profit', 'count': withheld,
            'message': (
                'Unknown cost or mixed currencies withhold '
                'figures. ' + NOT_CONSOLIDATED
            ),
            'drill_down': '/reports/profitability/',
        }] if withheld else []) + ([{
            'code': 'provisional_cost', 'count': provisional_rows,
            'message': (
                'These lots include a cost that is still provisional because '
                'its batch has not finalized its output, so their profit can move.'
            ),
            'drill_down': '/reports/profitability/',
        }] if provisional_rows else []),
    )


def _rows(workspace, lots):
    """Turn the accumulated per-lot figures into report rows."""
    found = {
        lot.pk: lot for lot in StockLot.objects.filter(
            workspace=workspace, pk__in=[key[0] for key in lots if key[0]],
        ).select_related('item')
    }
    rows = []
    for (lot_id, variety_id), entry in lots.items():
        lot = found.get(lot_id)
        complete = not entry['incomplete']
        profit = entry['net_sales'] - entry['direct_cogs'] - entry['production_loss']
        rows.append({
            'lot_id': lot_id,
            'lot_identifier': lot.identifier if lot else None,
            'item_name': lot.item.name if lot else 'No seed lot recorded',
            'variety_id': variety_id,
            'variety_name': entry['variety_name'],
            'batches': entry['batches'],
            'seed_quantity': decimal_string(entry['seed_quantity'], 4),
            'currency_code': next(iter(entry['codes'])) if len(entry['codes']) == 1 else None,
            'net_sales': decimal_string(entry['net_sales'], 4),
            'direct_cogs': decimal_string(entry['direct_cogs'], 4),
            'production_loss': decimal_string(entry['production_loss'], 4),
            'realized_profit': decimal_string(profit, 4) if complete else None,
            'committed_units': decimal_string(entry['committed_units'], 2),
            'committed_revenue': decimal_string(entry['committed_revenue'], 4),
            'pending_units': decimal_string(entry['pending_units'], 2),
            'pending_cost': decimal_string(entry['pending_cost'], 4) if complete else None,
            'provisional': entry['provisional'],
            'complete': complete,
        })
    return rows


def _variety_totals(rows):
    """Sum each variety's complete rows; an incomplete one is counted, not added."""
    totals = {}
    for row in rows:
        entry = totals.setdefault(row['variety_id'], {
            'variety_id': row['variety_id'], 'variety_name': row['variety_name'],
            'currency_code': row['currency_code'], 'lots': 0, 'incomplete_lots': 0,
            'realized_profit': ZERO, 'committed_revenue': ZERO, 'pending_cost': ZERO,
        })
        entry['lots'] += 1
        if not row['complete'] or row['currency_code'] != entry['currency_code']:
            entry['incomplete_lots'] += 1
            continue
        entry['realized_profit'] += Decimal(row['realized_profit'])
        entry['committed_revenue'] += Decimal(row['committed_revenue'])
        entry['pending_cost'] += Decimal(row['pending_cost'])
    money = ('realized_profit', 'committed_revenue', 'pending_cost')
    return [
        {**entry, **{field: decimal_string(entry[field], 4) for field in money}}
        for entry in totals.values()
    ]
