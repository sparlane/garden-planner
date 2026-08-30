"""Production outcome and input-cost reconciliation reports."""

from collections import Counter
from decimal import Decimal

from django.db.models import Q

from costing.services import batch_cost_breakdown
from plantings.germination import germination_summaries
from plantings.lifecycle import lifecycle_summaries
from plantings.loss import LOSS_CAUSES, batch_loss_by_cause
from plantings.models import (
    CohortOperation,
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    PlantCohort,
    ProductionBatch,
    SeedTrayPlanting,
    SpecificPlant,
)

from .common import Report, decimal_string


def _sown_quantity(batch):
    """Total controlled sowing quantity across every supported planting shape."""
    total = 0
    for model in (
            SeedTrayPlanting, GardenRowDirectSowPlanting,
            GardenSquareDirectSowPlanting):
        total += sum(model.objects.filter(batch=batch).values_list('quantity', flat=True))
    return total


def _original_cohort_output(batch):
    """Count observed cohort output once, including later losses and promotions."""
    return sum(
        event.quantity_after
        for operation in CohortOperation.objects.filter(
            workspace=batch.workspace,
            action=CohortOperation.Action.OBSERVE,
            events__cohort__batch=batch,
        ).prefetch_related('events')
        for event in operation.events.all()
        if event.cohort.batch_id == batch.pk
    )


def _batch_germination(batch):
    """Total this batch's tray sowings, and say whether the total is final.

    Only tray sowings answer the question: a direct-sown row produces a crop
    rather than a countable set of seedlings, which is the same distinction
    `costing.allocation.unattributable_share` draws about its cost. A batch
    with one open sowing among four has a provisional rate, because the one
    that can still rise is enough to make the total a floor.
    """
    sown = observed = ungerminated = 0
    open_sowings = closed_sowings = 0
    sowings = list(SeedTrayPlanting.objects.filter(batch=batch).order_by('pk'))
    for summary in germination_summaries(sowings).values():
        sown += summary['sown_quantity']
        observed += summary['observed_count']
        ungerminated += summary['ungerminated']
        if summary['provisional']:
            open_sowings += 1
        else:
            closed_sowings += 1
    return {
        'germination_sown': sown,
        'germination_observed': observed,
        'germination_ungerminated': ungerminated,
        'germination_rate': (
            decimal_string(Decimal(observed) / Decimal(sown), 6) if sown else None
        ),
        'germination_provisional': open_sowings > 0,
        'germination_open_sowings': open_sowings,
        'germination_closed_sowings': closed_sowings,
    }


def _batch_row(batch):  # pylint: disable=too-many-locals
    plants = list(SpecificPlant.objects.filter(batch=batch).order_by('pk'))
    summaries = lifecycle_summaries([plant.pk for plant in plants])
    states = Counter(summary.state for summary in summaries.values())
    cohorts = list(PlantCohort.objects.filter(batch=batch))
    cohort_states = Counter()
    for cohort in cohorts:
        cohort_states[cohort.lifecycle_state] += cohort.quantity
    identified_output = sum(plant.promoted_from_cohort_id is None for plant in plants)
    original_output = identified_output + _original_cohort_output(batch)
    current_output = sum(
        count for state, count in states.items()
        if state not in {'failed', 'lost', 'culled', 'donated', 'harvested', 'sold', 'discarded'}
    ) + sum(cohort.quantity for cohort in cohorts)
    sown = _sown_quantity(batch)
    losses = batch_loss_by_cause(batch)
    cost = batch_cost_breakdown(batch)
    total = cost['final_total'] or cost['provisional_total']
    unit_cost = None
    if total is not None and original_output:
        unit_cost = Decimal(total) / Decimal(original_output)
    return {
        'batch_id': batch.pk,
        'batch_code': batch.code,
        'variety_id': batch.variety_id,
        'variety_name': batch.variety.name,
        'status': batch.status,
        'actual_start': batch.actual_start,
        'output_finalized_at': batch.output_finalized_at,
        'sown_quantity': sown,
        'original_output': original_output,
        'output_rate': (
            decimal_string(Decimal(original_output) / Decimal(sown), 6)
            if sown else None
        ),
        'current_seedlings': current_output,
        'individual_states': dict(sorted(states.items())),
        'cohort_states': dict(sorted(cohort_states.items())),
        'loss_by_cause': losses,
        'loss_quantity': sum(losses.values()),
        **_batch_germination(batch),
        'production_loss': cost['totals']['production_loss'],
        'plant_inventory_value': cost['totals']['plant_inventory'],
        'cogs_value': cost['totals']['cogs'],
        'unresolved_value': cost['totals']['unresolved'],
        'unattributed_value': cost['totals']['unattributed'],
        'provisional_total': cost['provisional_total'],
        'final_total': cost['final_total'],
        'unit_cost': decimal_string(unit_cost, 4),
        'currency_code': cost['currency_code'],
        'provisional': cost['provisional'],
        'unvalued': cost['unknown_cost'],
        'input_layers': cost['layers'],
        'reconciliation': cost['totals'],
    }


def production_batches(workspace, filters):
    """Report batch inputs, output rates, current stock, outcomes, and costs."""
    queryset = ProductionBatch.objects.filter(workspace=workspace).select_related(
        'variety',
    )
    if filters.get('batch'):
        queryset = queryset.filter(pk=filters['batch'])
    if filters.get('variety'):
        queryset = queryset.filter(variety_id=filters['variety'])
    if filters.get('date_from'):
        queryset = queryset.filter(actual_start__date__gte=filters['date_from'])
    if filters.get('date_to'):
        queryset = queryset.filter(actual_start__date__lte=filters['date_to'])
    if filters.get('location'):
        queryset = queryset.filter(
            Q(
                specific_plants__locations__location_id=filters['location'],
                specific_plants__locations__ended__isnull=True,
            ) | Q(cohorts__location_id=filters['location']),
        )
    if filters.get('garden_square'):
        queryset = queryset.filter(
            specific_plants__locations__garden_square_id=filters['garden_square'],
            specific_plants__locations__ended__isnull=True,
        )
    rows = [_batch_row(batch) for batch in queryset.distinct().order_by('-created', '-pk')]
    provisional = sum(row['provisional'] for row in rows)
    unvalued = sum(row['unvalued'] for row in rows)
    unspecified = sum(
        row['loss_by_cause'][CohortOperation.LossCause.UNSPECIFIED.value] for row in rows
    )
    quality = []
    if unspecified:
        quality.append({
            'code': 'unspecified_loss_cause', 'count': unspecified,
            'message': (
                'These units were lost before a cause was recorded, so they are '
                'totalled apart from the causes rather than guessed at.'
            ),
            'drill_down': '/plantings/cohorts/?loss_cause=unspecified',
        })
    open_germination = sum(row['germination_open_sowings'] for row in rows)
    if open_germination:
        quality.append({
            'code': 'provisional_germination_rate', 'count': open_germination,
            'message': (
                'These sowings have not been declared finished germinating, so '
                'the batch germination rate is a floor rather than a result.'
            ),
            'drill_down': '/reports/germination/?provisional=true',
        })
    if provisional:
        quality.append({
            'code': 'provisional_production_cost', 'count': provisional,
            'message': 'Open output keeps production cost provisional.',
            'drill_down': '/reports/production-batches/?provisional=true',
        })
    if unvalued:
        quality.append({
            'code': 'unvalued_production_input', 'count': unvalued,
            'message': 'One or more exact input lots have unknown cost.',
            'drill_down': '/reports/production-batches/?unvalued=true',
        })
    return Report(
        name='production-batches', filters=filters, rows=rows,
        columns=tuple(rows[0]) if rows else (
            'batch_id', 'batch_code', 'variety_id', 'variety_name', 'status',
            'actual_start', 'output_finalized_at', 'sown_quantity',
            'original_output', 'output_rate', 'current_seedlings',
            'individual_states', 'cohort_states', 'loss_by_cause',
            'loss_quantity', 'germination_sown', 'germination_observed',
            'germination_ungerminated', 'germination_rate',
            'germination_provisional', 'germination_open_sowings',
            'germination_closed_sowings', 'production_loss',
            'plant_inventory_value', 'cogs_value', 'unresolved_value',
            'unattributed_value', 'provisional_total', 'final_total', 'unit_cost',
            'currency_code', 'provisional', 'unvalued', 'input_layers',
            'reconciliation',
        ),
        totals={
            'batches': len(rows),
            'current_seedlings': sum(row['current_seedlings'] for row in rows),
            'loss_by_cause': {
                cause.value: sum(row['loss_by_cause'][cause.value] for row in rows)
                for cause in LOSS_CAUSES
            },
            'loss_quantity': sum(row['loss_quantity'] for row in rows),
            'germination_sown': sum(row['germination_sown'] for row in rows),
            'germination_observed': sum(row['germination_observed'] for row in rows),
            'germination_ungerminated': sum(row['germination_ungerminated'] for row in rows),
            'germination_open_sowings': open_germination,
            'provisional_batches': provisional,
            'unvalued_batches': unvalued,
        },
        reconciliation={
            'cost_equation': (
                'total = plant inventory + COGS + production loss + unresolved '
                '+ unattributed + harvested output'
            ),
            'loss_equation': (
                'loss quantity = failed + lost + culled + donated + unspecified, '
                'counting anonymous cohort units and identified plants in the '
                'same vocabulary'
            ),
        },
        data_quality=quality,
    )
