"""Observed germination rate per sowing, batch, and variety.

Every figure here carries whether it is final. An open sowing's rate is a
running total that can only rise, so reading it beside a closed one without
saying which is which invites a re-sow decision made against a number that was
never finished. `plantings.germination` is where a sowing is declared done;
this module is where the consequence is read.

The ungerminated remainder is counted in seeds, not plants, and is deliberately
kept out of the plant loss totals in `plantings.loss` — a seed that never came
up was never a unit of stock, and adding it to a plant count would make the
production report's loss equation stop reconciling. It carries a cause from the
same vocabulary so the two can still be read together.
"""

from collections import defaultdict
from decimal import Decimal

from plantings.germination import germination_summaries
from plantings.models import SeedTrayPlanting

from .common import Report, decimal_string


RATE_PLACES = 6


def _rate(observed, sown):
    """Return an exact rate, or None when no seed was placed in a cell."""
    if not sown:
        return None
    return decimal_string(Decimal(observed) / Decimal(sown), RATE_PLACES)


def _sowing_row(sowing, summary):
    """Describe one sowing's germination, marked provisional while it is open."""
    variety = sowing.batch.variety
    return {
        'sowing_id': sowing.pk,
        'batch_id': sowing.batch_id,
        'batch_code': sowing.batch.code,
        'variety_id': variety.pk,
        'variety_name': variety.name,
        'seed_tray_id': sowing.seed_tray_id,
        'planted': sowing.planted,
        'sown_quantity': summary['sown_quantity'],
        'observed_count': summary['observed_count'],
        'ungerminated': summary['ungerminated'],
        'germination_rate': _rate(summary['observed_count'], summary['sown_quantity']),
        'provisional': summary['provisional'],
        'closed_at': summary['closed_at'],
        'closed_observed_count': summary['closed_observed_count'],
        'closed_ungerminated': summary['closed_ungerminated'],
        'loss_cause': summary['loss_cause'],
        'late_germinations': summary['late_germinations'],
    }


def _variety_totals(rows):
    """Total each variety twice: everything on file, and the closed sowings.

    Sample size is part of the answer. A variety with two sowings behind it is
    not evidence, and a rate shown without the count it rests on invites the
    kind of overreaction task 99's variance report exists to prevent.
    """
    grouped = defaultdict(lambda: {
        'sowings': 0, 'closed_sowings': 0,
        'sown_quantity': 0, 'observed_count': 0, 'ungerminated': 0,
        'closed_sown_quantity': 0, 'closed_observed_count': 0,
    })
    names = {}
    for row in rows:
        names[row['variety_id']] = row['variety_name']
        totals = grouped[row['variety_id']]
        totals['sowings'] += 1
        totals['sown_quantity'] += row['sown_quantity']
        totals['observed_count'] += row['observed_count']
        totals['ungerminated'] += row['ungerminated']
        if not row['provisional']:
            totals['closed_sowings'] += 1
            totals['closed_sown_quantity'] += row['sown_quantity']
            totals['closed_observed_count'] += row['observed_count']
    return [
        {
            'variety_id': variety_id,
            'variety_name': names[variety_id],
            **totals,
            'germination_rate': _rate(totals['observed_count'], totals['sown_quantity']),
            'final_germination_rate': _rate(
                totals['closed_observed_count'], totals['closed_sown_quantity'],
            ),
        }
        for variety_id, totals in sorted(grouped.items(), key=lambda item: names[item[0]])
    ]


def _filtered_sowings(workspace, filters):
    """Apply the report's dimensions to the tray sowings on file."""
    queryset = SeedTrayPlanting.objects.filter(workspace=workspace).select_related(
        'batch__variety',
    )
    if filters.get('batch'):
        queryset = queryset.filter(batch_id=filters['batch'])
    if filters.get('variety'):
        queryset = queryset.filter(batch__variety_id=filters['variety'])
    if filters.get('seed_tray'):
        queryset = queryset.filter(seed_tray_id=filters['seed_tray'])
    if filters.get('date_from'):
        queryset = queryset.filter(planted__date__gte=filters['date_from'])
    if filters.get('date_to'):
        queryset = queryset.filter(planted__date__lte=filters['date_to'])
    return queryset.order_by('-planted', '-pk')


def germination_rates(workspace, filters):
    """Report each tray sowing's observed germination and whether it is final."""
    sowings = list(_filtered_sowings(workspace, filters))
    summaries = germination_summaries(sowings)
    rows = [_sowing_row(sowing, summaries[sowing.pk]) for sowing in sowings]
    if filters.get('provisional') is not None:
        rows = [row for row in rows if row['provisional'] == filters['provisional']]
    provisional = sum(row['provisional'] for row in rows)
    sown = sum(row['sown_quantity'] for row in rows)
    observed = sum(row['observed_count'] for row in rows)
    closed_sown = sum(row['sown_quantity'] for row in rows if not row['provisional'])
    closed_observed = sum(row['observed_count'] for row in rows if not row['provisional'])
    quality = []
    if provisional:
        quality.append({
            'code': 'provisional_germination_rate', 'count': provisional,
            'message': (
                'These sowings have not been declared finished germinating, so '
                'their rate can still rise and is not a final figure.'
            ),
            'drill_down': '/reports/germination/?provisional=true',
        })
    return Report(
        name='germination', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'sowing_id', 'batch_id', 'batch_code', 'variety_id', 'variety_name',
            'seed_tray_id', 'planted', 'sown_quantity', 'observed_count',
            'ungerminated', 'germination_rate', 'provisional', 'closed_at',
            'closed_observed_count', 'closed_ungerminated', 'loss_cause',
            'late_germinations',
        ),
        totals={
            'sowings': len(rows),
            'provisional_sowings': provisional,
            'closed_sowings': len(rows) - provisional,
            'sown_quantity': sown,
            'observed_count': observed,
            'ungerminated': sum(row['ungerminated'] for row in rows),
            'germination_rate': _rate(observed, sown),
            'final_sown_quantity': closed_sown,
            'final_observed_count': closed_observed,
            'final_germination_rate': _rate(closed_observed, closed_sown),
            'late_germinations': sum(row['late_germinations'] for row in rows),
            'by_variety': _variety_totals(rows),
        },
        reconciliation={
            'rate_equation': (
                'germination rate = seedlings observed / seed placed in cells, '
                'counting every seedling the sowing ever produced'
            ),
            'provisional_note': (
                'A provisional rate counts seedlings against a sowing nobody '
                'has declared finished, so it is a floor rather than a result. '
                'The final columns total the closed sowings alone.'
            ),
        },
        data_quality=quality,
    )
