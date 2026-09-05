"""Every planning assumption beside the crop it actually sized.

`plantings.assumption_variance` does the comparing; this module is the report
envelope around it, so the same figures reach the screen, the CSV export and
the nursery's other reports in one shape.

Nothing here revises anything. The report's job is to make a gap unmissable —
an assumed germination rate of 0.85 against an observed 0.6 under-sows every
cycle — and to say how much evidence the gap rests on, so a grower decides
whether last season's mean is the right judgement about the next one.
"""

from plantings.assumption_variance import assumption_variance_rows

from .common import Report


COLUMNS = (
    'assumption_id', 'variety_id', 'variety_name', 'effective_from',
    'effective_until', 'superseded_by', 'batches', 'first_sown', 'last_sown',
    'minimum_samples', 'sample_sufficient', 'tolerance_percent',
    'assumed_germination_rate', 'observed_germination_rate',
    'germination_variance', 'germination_diverged', 'germination_sown',
    'germination_observed', 'germination_sowings', 'germination_open_sowings',
    'assumed_tray_density', 'observed_tray_density', 'tray_density_variance',
    'tray_density_diverged', 'tray_fills', 'tray_fills_shared',
    'unstaged_losses', 'mixed_population_batches', 'stages', 'diverged',
    'divergences',
)


def _quality(rows):
    """Publish what would make a reader misread a variance, not just the gaps."""
    quality = []
    flagged = [row for row in rows if row['diverged']]
    if flagged:
        quality.append({
            'code': 'diverged_assumption', 'count': len(flagged),
            'message': (
                'These assumptions have diverged from what was observed under '
                'them by more than the workspace tolerance, and are still the '
                'figures production is planned from.'
            ),
            'drill_down': '/reports/assumption-variance/?diverged=true',
        })
    thin = [
        row for row in rows if row['batches'] and not row['sample_sufficient']
    ]
    if thin:
        quality.append({
            'code': 'thin_assumption_sample', 'count': len(thin),
            'message': (
                'These assumptions have fewer batches behind them than the '
                'workspace requires before flagging one, so their variance is '
                'reported but never raised as a finding.'
            ),
            'drill_down': '/reports/assumption-variance/',
        })
    open_sowings = sum(row['germination_open_sowings'] for row in rows)
    if open_sowings:
        quality.append({
            'code': 'provisional_germination_rate', 'count': open_sowings,
            'message': (
                'These sowings have not been declared finished germinating, so '
                'they are left out of the observed rate rather than dragging a '
                'figure down that can still rise.'
            ),
            'drill_down': '/reports/germination/?provisional=true',
        })
    unstaged = sum(row['unstaged_losses'] for row in rows)
    if unstaged:
        quality.append({
            'code': 'unstaged_loss', 'count': unstaged,
            'message': (
                'These units were lost with no stage observation standing, so '
                'they are totalled apart from the stage loss rates rather than '
                'assigned to a stage that was never recorded.'
            ),
            'drill_down': '/reports/production-batches/',
        })
    mixed = sum(row['mixed_population_batches'] for row in rows)
    if mixed:
        quality.append({
            'code': 'mixed_stage_population', 'count': mixed,
            'message': (
                'These batches were promoted from a cohort partway, so a unit '
                'can be counted twice for the stage it was standing in and the '
                'stage loss rate reads low.'
            ),
            'drill_down': '/reports/production-batches/',
        })
    return quality


def assumption_variance(workspace, filters):
    """Report each assumption version against the batches sown under it."""
    rows = assumption_variance_rows(
        workspace,
        variety=filters.get('variety'),
        assumption=filters.get('assumption'),
        date_from=filters.get('date_from'),
        date_to=filters.get('date_to'),
    )
    if filters.get('diverged') is not None:
        rows = [row for row in rows if row['diverged'] == filters['diverged']]
    return Report(
        name='assumption-variance', filters=filters, rows=rows, columns=COLUMNS,
        totals={
            'assumptions': len(rows),
            'diverged_assumptions': sum(row['diverged'] for row in rows),
            'assumptions_with_evidence': sum(bool(row['batches']) for row in rows),
            'batches': sum(row['batches'] for row in rows),
            'germination_sowings': sum(row['germination_sowings'] for row in rows),
            'germination_open_sowings': sum(
                row['germination_open_sowings'] for row in rows
            ),
            'tray_fills': sum(row['tray_fills'] for row in rows),
            'unstaged_losses': sum(row['unstaged_losses'] for row in rows),
        },
        reconciliation={
            'attribution': (
                'A batch is measured against the assumption version that sized '
                'it: the one its approved plan requirement names, or failing '
                'that the version in force on the day it was first sown'
            ),
            'divergence_equation': (
                'diverged = |observed - assumed| / assumed > the workspace '
                'tolerance percent, and only once the batches behind the '
                'figure reach the workspace minimum sample'
            ),
            'revision_note': (
                'Nothing here revises an assumption. A planning figure is a '
                'judgement about next season rather than an average of the '
                'last one, so the revision is offered pre-filled and written '
                'only when an operator accepts it'
            ),
        },
        data_quality=_quality(rows),
    )
