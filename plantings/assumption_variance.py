"""What actually happened, set beside the assumption that predicted it.

`NurseryPlanningAssumption` decides how much seed is bought and how many trays
are filled. Nothing used to compare it with the crop it sized, so a variety
whose real germination was 0.6 against an assumed 0.85 under-sowed every cycle
and the shortfall surfaced months later as unmet demand, with nothing pointing
back at the figure that caused it.

This module is that comparison, and only the comparison. It never writes an
assumption: a planning figure is a commercial judgement about next season, and
a grower who has changed media or supplier knows something last season's mean
does not. `revision_draft` pre-fills a new version for an operator to accept or
edit; `revise_assumption` writes only what they submit.

**Attribution.** An assumption is effective-dated, so the actuals belonging to
it are the batches sown while it was in force — not everything on file for the
variety. A batch created from an approved plan carries a stronger answer than
its dates do: `NurseryPlanRequirement.assumption` names the version that
actually sized it, and it wins, so a batch planned under one version and sown
after another took effect is still measured against the one that predicted it.

**Sample size travels with every figure.** Three batches is not evidence.
Each observation carries the count behind it, and no divergence is flagged
until the workspace's `assumption_minimum_samples` is met.

**What is excluded, and why.** A sowing nobody has declared finished
germinating has a rate that can only rise (`plantings.germination`), so it is
left out of the observed rate and counted separately rather than dragging the
figure down. A loss taken while the stock had no stage observation cannot be
attributed to a stage, so it is totalled as `unstaged_losses` rather than
guessed at. A tray fill shared with another variety is not the density this
assumption describes, so it is counted apart too.

**One known approximation.** Stage entry counts read identified plants and
anonymous cohorts side by side. A batch promoted partway can therefore count a
unit twice for the stage it was standing in when it was promoted, because the
cohort held it at its own entry and the plant is observed in that stage again
under its own identity. The report publishes the affected batch count as a
data-quality finding instead of silently absorbing it into a loss rate.
"""

# The observation builders keep one assumption's facts together, and the
# comparison and revision services name every figure they take, which is what
# makes them readable beside the report row they produce.
# pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Min, Q
from django.utils import timezone

from .germination import germination_summaries
from .loss import LOSS_EVENTS
from .models import (
    CohortEvent,
    CohortOperation,
    GardenRowDirectSowPlanting,
    GardenSquareDirectSowPlanting,
    NurseryObservationTarget,
    NurseryPlanRequirement,
    NurseryPlanningAssumption,
    NurseryPlanningInputAssumption,
    NurseryPlanningStageAssumption,
    PlantCohort,
    PlantLifecycleEvent,
    ProductionBatch,
    SeedTrayPlanting,
    SpecificPlant,
)


#: Rates are rendered at the scale `germination_rate` is stored at, so the
#: assumed and observed figures can be compared digit for digit.
RATE_PLACES = 6

#: Durations are averages of whole recorded days, so they carry two places
#: rather than pretending to the precision a stored `lead_days` has.
DAY_PLACES = 2

SOWING_MODELS = (
    SeedTrayPlanting, GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting,
)


def _decimal_string(value, places):
    """Render a decimal at a stable scale without passing a float through."""
    if value is None:
        return None
    return f'{Decimal(value):.{places}f}'


def _mean(values):
    """Return the arithmetic mean of a sequence, or None when it is empty."""
    if not values:
        return None
    return Decimal(sum(values)) / Decimal(len(values))


def _local_day(workspace, moment):
    """Read one instant as the calendar day the nursery was working on.

    An assumption's effective range is a run of local dates, so a sowing made
    late in the evening has to fall on the day the grower would name it,
    not on whatever day it is in the server's own zone.
    """
    return moment.astimezone(ZoneInfo(workspace.timezone)).date()


def _sown_on(workspace, batches):
    """Return the day each batch was first sown, by batch identifier.

    A batch that has been created but never sown has not been produced under
    any assumption, so it is absent here and drops out of the comparison.
    """
    earliest = {}
    ids = [batch.pk for batch in batches]
    for model in SOWING_MODELS:
        rows = (
            model.objects.filter(batch_id__in=ids)
            .values('batch_id').annotate(first=Min('planted'))
        )
        for row in rows:
            day = _local_day(workspace, row['first'])
            current = earliest.get(row['batch_id'])
            if current is None or day < current:
                earliest[row['batch_id']] = day
    return earliest


def _planned_assumptions(workspace):
    """Return the assumption version each plan-created batch was sized from."""
    return dict(
        NurseryPlanRequirement.objects
        .filter(demand__plan__workspace=workspace, batch__isnull=False)
        .values_list('batch_id', 'assumption_id')
    )


def _covers(assumption, day):
    """Whether an assumption's explicit effective range covers a day."""
    if day < assumption.effective_from:
        return False
    return assumption.effective_until is None or day <= assumption.effective_until


def attribute_batches(workspace, assumptions):
    """Group batches under the assumption version that predicted each one.

    Returns `{assumption_id: [ProductionBatch]}` and the day each batch was
    sown, which is what anchors the review task in `work.projections`.
    """
    assumptions = list(assumptions)
    by_id = {assumption.pk: assumption for assumption in assumptions}
    by_variety = defaultdict(list)
    for assumption in assumptions:
        by_variety[assumption.variety_id].append(assumption)
    for versions in by_variety.values():
        versions.sort(key=lambda row: (row.effective_from, row.pk), reverse=True)
    batches = list(
        ProductionBatch.objects
        .filter(workspace=workspace, variety_id__in=by_variety)
        .exclude(status=ProductionBatch.Status.CANCELLED)
        .select_related('variety')
    )
    sown_on = _sown_on(workspace, batches)
    planned = _planned_assumptions(workspace)
    grouped = {assumption.pk: [] for assumption in assumptions}
    for batch in batches:
        day = sown_on.get(batch.pk)
        if day is None:
            continue
        chosen = by_id.get(planned.get(batch.pk))
        if chosen is None:
            chosen = next(
                (row for row in by_variety[batch.variety_id] if _covers(row, day)), None,
            )
        if chosen is not None:
            grouped[chosen.pk].append(batch)
    return grouped, sown_on


def _germination_facts(batches):
    """Observe germination over the closed sowings of these batches alone."""
    sowings = list(SeedTrayPlanting.objects.filter(batch__in=batches).order_by('pk'))
    sown = observed = closed = provisional = 0
    for summary in germination_summaries(sowings).values():
        if summary['provisional']:
            provisional += 1
            continue
        closed += 1
        sown += summary['sown_quantity']
        observed += summary['observed_count']
    return {
        'germination_sown': sown,
        'germination_observed': observed,
        'germination_sowings': closed,
        'germination_open_sowings': provisional,
        'observed_germination_rate': (
            Decimal(observed) / Decimal(sown) if sown else None
        ),
    }


def _tray_density_facts(batches, variety_id):
    """Observe clusters per tray over the fills this variety had to itself.

    A fill shared with another crop is not the density the assumption
    describes — planning sizes trays as though the whole tray were this
    variety — so it is counted apart rather than averaged in. That is why the
    whole fill is read rather than only this batch's share of it: the other
    crop's clusters are what make the fill the wrong thing to average.

    A fill is a tray and the generation that named its filling. Sowings made
    into a tray before generations existed have no generation to separate
    them, so they read as one fill, which is all the record supports.
    """
    keys = set(
        SeedTrayPlanting.objects
        .filter(batch__in=batches, seed_tray__isnull=False)
        .values_list('seed_tray_id', 'generation_id')
    )
    if not keys:
        return {'observed_tray_density': None, 'tray_fills': 0, 'tray_fills_shared': 0}
    fills = defaultdict(lambda: {'clusters': 0, 'varieties': set()})
    for tray_id, generation_id, quantity, sown_variety in SeedTrayPlanting.objects.filter(
            seed_tray_id__in={tray_id for tray_id, _generation in keys},
    ).values_list('seed_tray_id', 'generation_id', 'quantity', 'batch__variety_id'):
        if (tray_id, generation_id) not in keys:
            continue
        fill = fills[(tray_id, generation_id)]
        fill['clusters'] += quantity
        fill['varieties'].add(sown_variety)
    exclusive = [
        fill['clusters'] for fill in fills.values() if fill['varieties'] == {variety_id}
    ]
    return {
        'observed_tray_density': _mean(exclusive),
        'tray_fills': len(exclusive),
        'tray_fills_shared': len(fills) - len(exclusive),
    }


def _stage_history(batches):
    """Return each target's uncorrected stage observations, oldest first."""
    ids = [batch.pk for batch in batches]
    rows = NurseryObservationTarget.objects.filter(
        Q(plant__batch_id__in=ids) | Q(cohort__batch_id__in=ids),
        observation__correction__isnull=True,
        observation__stage__isnull=False,
    ).values_list(
        'plant_id', 'cohort_id', 'observation__stage_id', 'observation__occurred_at',
    )
    history = defaultdict(list)
    for plant_id, cohort_id, stage_id, occurred_at in rows:
        target = ('plant', plant_id) if plant_id else ('cohort', cohort_id)
        history[target].append((occurred_at, stage_id))
    for entries in history.values():
        entries.sort()
    return history


def _stage_runs(entries):
    """Collapse repeated observations into one entry per stage occupied."""
    runs = []
    for occurred_at, stage_id in entries:
        if runs and runs[-1][0] == stage_id:
            continue
        runs.append((stage_id, occurred_at))
    return runs


def _cohort_quantities(batches):
    """Return each cohort's audited quantity timeline, oldest first."""
    timeline = defaultdict(list)
    rows = CohortEvent.objects.filter(cohort__batch__in=batches).values_list(
        'cohort_id', 'operation__occurred_at', 'quantity_before', 'quantity_after',
    )
    for cohort_id, occurred_at, before, after in rows:
        timeline[cohort_id].append((occurred_at, before, after))
    for entries in timeline.values():
        entries.sort(key=lambda row: row[0])
    return timeline


def _quantity_at(timeline, cohort_id, moment, fallback):
    """Return the cohort's counted quantity at one moment in its history."""
    entries = timeline.get(cohort_id)
    if not entries:
        return fallback
    quantity = entries[0][1]
    for occurred_at, _before, after in entries:
        if occurred_at > moment:
            break
        quantity = after
    return quantity


def _stage_at(runs, moment):
    """Return the stage a target was standing in at one moment, or None."""
    stage = None
    for stage_id, occurred_at in runs:
        if occurred_at > moment:
            break
        stage = stage_id
    return stage


def _loss_events(batches):
    """Return every surviving loss as `(target, moment, units)`.

    Identified plants and anonymous cohorts lose stock for the same reasons
    and are counted here in the same units, which is the distinction
    `plantings.loss` exists to keep from mattering.
    """
    events = []
    for plant_id, occurred_at in PlantLifecycleEvent.objects.filter(
            batch__in=batches, event_type__in=LOSS_EVENTS, reversal__isnull=True,
    ).values_list('plant_id', 'occurred_at'):
        events.append((('plant', plant_id), occurred_at, 1))
    for cohort_id, occurred_at, delta in CohortEvent.objects.filter(
            cohort__batch__in=batches,
            operation__action=CohortOperation.Action.LOSS,
            quantity_delta__lt=0,
    ).values_list('cohort_id', 'operation__occurred_at', 'quantity_delta'):
        events.append((('cohort', cohort_id), occurred_at, abs(delta)))
    return events


def _stage_facts(batches, stage_ids):
    """Observe how long each stage took and how much it lost.

    Duration counts only intervals a later observation has closed, so the
    stage a crop is standing in right now contributes nothing rather than
    reporting the time it has spent there so far as the time it takes.
    """
    durations = defaultdict(list)
    entered = defaultdict(int)
    lost = defaultdict(int)
    unstaged = 0
    history = _stage_history(batches)
    quantities = _cohort_quantities(batches)
    cohort_totals = dict(
        PlantCohort.objects.filter(batch__in=batches).values_list('pk', 'quantity')
    )
    runs_by_target = {target: _stage_runs(entries) for target, entries in history.items()}
    for target, runs in runs_by_target.items():
        kind, identity = target
        for index, (stage_id, occurred_at) in enumerate(runs):
            if index + 1 < len(runs):
                durations[stage_id].append((runs[index + 1][1] - occurred_at).days)
            if kind == 'plant':
                units = 1
            else:
                units = _quantity_at(
                    quantities, identity, occurred_at, cohort_totals.get(identity, 0),
                )
            entered[stage_id] += units
    for target, occurred_at, units in _loss_events(batches):
        stage_id = _stage_at(runs_by_target.get(target, []), occurred_at)
        if stage_id is None:
            unstaged += units
        else:
            lost[stage_id] += units
    return {
        'durations': {stage_id: durations.get(stage_id, []) for stage_id in stage_ids},
        'entered': {stage_id: entered.get(stage_id, 0) for stage_id in stage_ids},
        'lost': {stage_id: lost.get(stage_id, 0) for stage_id in stage_ids},
        'unstaged_losses': unstaged,
        'mixed_population': _mixed_population(batches),
    }


def _mixed_population(batches):
    """Count batches whose stage entries can double-count a promoted unit."""
    promoted = set(
        SpecificPlant.objects
        .filter(batch__in=batches, promoted_from_cohort__isnull=False)
        .values_list('batch_id', flat=True)
    )
    observed_cohorts = set(
        NurseryObservationTarget.objects
        .filter(cohort__batch__in=batches, observation__stage__isnull=False)
        .values_list('cohort__batch_id', flat=True)
    )
    return len(promoted & observed_cohorts)


def _gap(assumed, observed):
    """Return the signed difference and its size relative to the assumption.

    A zero assumption has no percentage to be wrong by — a planned loss rate
    of nought is a claim that nothing is lost — so any observed value above it
    is reported as a complete divergence rather than a division by zero.
    """
    if observed is None or assumed is None:
        return None, None
    difference = Decimal(observed) - Decimal(assumed)
    if not Decimal(assumed):
        return difference, (Decimal('100') if difference else Decimal('0'))
    return difference, abs(difference) / Decimal(assumed) * Decimal('100')


class Tolerance(NamedTuple):
    """How far a figure may drift, and how much evidence that needs."""

    percent: Decimal
    minimum_samples: int

    @classmethod
    def of(cls, workspace):
        """Read one workspace's configured tolerance for planning figures."""
        return cls(
            workspace.assumption_tolerance_percent,
            workspace.assumption_minimum_samples,
        )


class Comparison(NamedTuple):
    """One observed figure set beside the assumption that predicted it."""

    difference: object
    percent: object
    diverged: bool


def _comparison(assumed, observed, sample, tolerance):
    """Set one observed figure beside the assumption, and say if it diverged."""
    difference, percent = _gap(assumed, observed)
    enough = sample >= tolerance.minimum_samples
    return Comparison(difference, percent, bool(
        percent is not None and enough and percent > Decimal(tolerance.percent),
    ))


def _stage_rows(assumption, facts, tolerance):
    """Compare every stage's planned duration and loss with what happened."""
    rows = []
    for stage in assumption.stages.all():
        durations = facts['durations'][stage.stage_id]
        observed_days = _mean(durations)
        entered = facts['entered'][stage.stage_id]
        lost = facts['lost'][stage.stage_id]
        observed_loss = Decimal(lost) / Decimal(entered) if entered else None
        lead = _comparison(
            Decimal(stage.lead_days), observed_days, len(durations), tolerance,
        )
        loss = _comparison(stage.loss_rate, observed_loss, entered, tolerance)
        rows.append({
            'stage_id': stage.stage_id,
            'stage_name': stage.stage.name,
            'sequence': stage.sequence,
            'assumed_lead_days': stage.lead_days,
            'observed_lead_days': _decimal_string(observed_days, DAY_PLACES),
            'lead_days_variance': _decimal_string(lead.difference, DAY_PLACES),
            'lead_days_samples': len(durations),
            'lead_days_diverged': lead.diverged,
            'assumed_loss_rate': _decimal_string(stage.loss_rate, RATE_PLACES),
            'observed_loss_rate': _decimal_string(observed_loss, RATE_PLACES),
            'loss_rate_variance': _decimal_string(loss.difference, RATE_PLACES),
            'entered_units': entered,
            'lost_units': lost,
            'loss_rate_diverged': loss.diverged,
        })
    return rows


def _assumption_row(assumption, batches, sown_on, workspace, successor):
    """Describe one assumption version beside the crop it actually sized."""
    tolerance = Tolerance.of(workspace)
    germination = _germination_facts(batches)
    density = _tray_density_facts(batches, assumption.variety_id)
    stage_ids = [stage.stage_id for stage in assumption.stages.all()]
    facts = _stage_facts(batches, stage_ids)
    stages = _stage_rows(assumption, facts, tolerance)
    rate = _comparison(
        assumption.germination_rate, germination['observed_germination_rate'],
        len(batches), tolerance,
    )
    tray = _comparison(
        Decimal(assumption.tray_density), density['observed_tray_density'],
        density['tray_fills'], tolerance,
    )
    divergences = []
    if rate.diverged:
        divergences.append('germination_rate')
    if tray.diverged:
        divergences.append('tray_density')
    for stage in stages:
        if stage['lead_days_diverged']:
            divergences.append(f'stage:{stage["stage_id"]}:lead_days')
        if stage['loss_rate_diverged']:
            divergences.append(f'stage:{stage["stage_id"]}:loss_rate')
    days = [sown_on[batch.pk] for batch in batches if batch.pk in sown_on]
    return {
        'assumption_id': assumption.pk,
        'variety_id': assumption.variety_id,
        'variety_name': assumption.variety.name,
        'effective_from': assumption.effective_from,
        'effective_until': assumption.effective_until,
        'superseded_by': successor,
        'batches': len(batches),
        'first_sown': min(days) if days else None,
        'last_sown': max(days) if days else None,
        'minimum_samples': tolerance.minimum_samples,
        'sample_sufficient': len(batches) >= tolerance.minimum_samples,
        'tolerance_percent': _decimal_string(tolerance.percent, 4),
        'assumed_germination_rate': _decimal_string(
            assumption.germination_rate, RATE_PLACES,
        ),
        'observed_germination_rate': _decimal_string(
            germination['observed_germination_rate'], RATE_PLACES,
        ),
        'germination_variance': _decimal_string(rate.difference, RATE_PLACES),
        'germination_diverged': rate.diverged,
        'germination_sown': germination['germination_sown'],
        'germination_observed': germination['germination_observed'],
        'germination_sowings': germination['germination_sowings'],
        'germination_open_sowings': germination['germination_open_sowings'],
        'assumed_tray_density': assumption.tray_density,
        'observed_tray_density': _decimal_string(
            density['observed_tray_density'], DAY_PLACES,
        ),
        'tray_density_variance': _decimal_string(tray.difference, DAY_PLACES),
        'tray_density_diverged': tray.diverged,
        'tray_fills': density['tray_fills'],
        'tray_fills_shared': density['tray_fills_shared'],
        'unstaged_losses': facts['unstaged_losses'],
        'mixed_population_batches': facts['mixed_population'],
        'stages': stages,
        'diverged': bool(divergences),
        'divergences': divergences,
    }


def assumption_queryset(workspace):
    """Every assumption version, with the rows a comparison has to read."""
    return (
        NurseryPlanningAssumption.objects
        .filter(workspace=workspace)
        .select_related('variety')
        .prefetch_related('stages__stage', 'inputs__item')
    )


def _selected(assumption, variety, identity, date_from, date_to):
    """Whether one version falls inside the dimensions that were asked for."""
    if variety is not None and assumption.variety_id != variety:
        return False
    if identity is not None and assumption.pk != identity:
        return False
    if date_to is not None and assumption.effective_from > date_to:
        return False
    if date_from is not None and assumption.effective_until is not None:
        return assumption.effective_until >= date_from
    return True


def assumption_variance_rows(workspace, *, variety=None, assumption=None,
                             date_from=None, date_to=None):
    """Compare every assumption version with the batches sown under it.

    Filtering narrows which assumptions are reported, never which batches are
    attributed to one: a version measured against half its evidence would
    report a gap that only the filter created.
    """
    assumptions = list(assumption_queryset(workspace))
    grouped, sown_on = attribute_batches(workspace, assumptions)
    successors = {}
    by_variety = defaultdict(list)
    for row in assumptions:
        by_variety[row.variety_id].append(row)
    for versions in by_variety.values():
        ordered = sorted(versions, key=lambda row: (row.effective_from, row.pk))
        for index, row in enumerate(ordered[:-1]):
            successors[row.pk] = ordered[index + 1].pk
    selected = [row for row in assumptions if _selected(
        row, variety, assumption, date_from, date_to,
    )]
    return [
        _assumption_row(
            row, grouped[row.pk], sown_on, workspace, successors.get(row.pk),
        )
        for row in sorted(
            selected, key=lambda row: (row.variety.name, row.effective_from, row.pk),
        )
    ]


def _rounded(value):
    """Round an observed average to the whole unit an assumption is stored in."""
    if value is None:
        return None
    return int(Decimal(value).quantize(Decimal('1')))


def _draft_stage(stage, row):
    """Pre-fill one stage from what happened, falling back to what was assumed."""
    lead = _rounded(Decimal(row['observed_lead_days'])) if row and row['observed_lead_days'] else None
    loss = row['observed_loss_rate'] if row else None
    return {
        'stage': stage.stage_id,
        'stage_name': stage.stage.name,
        'sequence': stage.sequence,
        'lead_days': lead if lead is not None else stage.lead_days,
        'lead_days_source': 'observed' if lead is not None else 'assumed',
        'lead_days_samples': row['lead_days_samples'] if row else 0,
        'loss_rate': loss if loss is not None else _decimal_string(stage.loss_rate, RATE_PLACES),
        'loss_rate_source': 'observed' if loss is not None else 'assumed',
        'loss_rate_samples': row['entered_units'] if row else 0,
        'location': stage.location_id,
        'capacity_basis': stage.capacity_basis,
        'capacity_per_plant': str(stage.capacity_per_plant),
    }


def revision_draft(assumption):
    """Pre-fill the next version with what happened, for somebody to accept.

    Nothing here is saved. The draft is the observed figure wherever there is
    one and the standing judgement wherever there is not, so accepting it
    unchanged is a deliberate act rather than a default the system applied on
    an operator's behalf.

    An observed germination rate above one is real — a multigerm cluster
    yields more seedlings than it was sown — but the field is a rate the model
    caps at one, so the draft carries the cap and says it did rather than
    offering a version that cannot be saved. A rate of nought is the same
    problem from the other end: the field will not hold it, and a plan sized
    from it would ask for infinite seed, so the standing judgement is kept and
    the nought stays where it belongs, in the variance beside it.
    """
    workspace = assumption.workspace
    variance = assumption_variance_rows(workspace, assumption=assumption.pk)
    row = variance[0]
    observed_rate = row['observed_germination_rate']
    capped = observed_rate is not None and Decimal(observed_rate) > 1
    if capped:
        rate = _decimal_string(Decimal('1'), RATE_PLACES)
    elif observed_rate is not None and Decimal(observed_rate) > 0:
        rate = observed_rate
    else:
        rate = None
    observed_density = row['observed_tray_density']
    density = _rounded(Decimal(observed_density)) if observed_density else None
    if density is not None and density < 1:
        # A fill averaging under one cluster is a real record but not a
        # density a tray can be sized from, so the standing figure stands.
        density = None
    stages = {stage['stage_id']: stage for stage in row['stages']}
    today = _local_day(workspace, timezone.now())
    return {
        'assumption': assumption.pk,
        'variety': assumption.variety_id,
        'variety_name': assumption.variety.name,
        'effective_from': max(today, assumption.effective_from) + timedelta(days=1),
        'germination_rate': (
            rate if rate is not None
            else _decimal_string(assumption.germination_rate, RATE_PLACES)
        ),
        'germination_rate_source': 'observed' if rate is not None else 'assumed',
        'germination_rate_capped': capped,
        'seeds_per_cluster': assumption.seeds_per_cluster,
        'tray_density': density if density is not None else assumption.tray_density,
        'tray_density_source': 'observed' if density is not None else 'assumed',
        'stages': [
            _draft_stage(stage, stages.get(stage.stage_id))
            for stage in assumption.stages.all()
        ],
        'variance': row,
    }


@transaction.atomic
def revise_assumption(assumption, *, effective_from, germination_rate=None,
                      seeds_per_cluster=None, tray_density=None, notes='', stages=()):
    """Write the next effective-dated version from what an operator submitted.

    The values are the caller's, not this module's: a revision is only ever
    the figures somebody looked at and accepted. What the service adds is the
    part that is easy to get wrong by hand — closing the standing version on
    the day before the new one starts, and carrying the stages and inputs
    across so a revised assumption is not a half-built one.

    The superseded version is left otherwise untouched. Every plan calculated
    under it holds its own `assumption_snapshot`, so its history stays exactly
    as it was recorded.
    """
    assumption = (
        NurseryPlanningAssumption.objects.select_for_update().get(pk=assumption.pk)
    )
    if effective_from <= assumption.effective_from:
        raise ValidationError({
            'effective_from': 'A revision has to start after the version it replaces.',
        })
    existing = list(assumption.stages.select_related('stage').all())
    submitted = {row['stage']: row for row in stages}
    unknown = sorted(submitted.keys() - {row.stage_id for row in existing})
    if unknown:
        raise ValidationError({
            'stages': 'One or more stages do not belong to this assumption.',
        })
    # The revision takes over the rest of the window the old version held. A
    # window that has already ended by the new start date is not a window to
    # inherit, so the revision is open-ended rather than born invalid.
    remaining = assumption.effective_until
    if remaining is not None and remaining < effective_from:
        remaining = None
    revision = NurseryPlanningAssumption.objects.create(
        workspace=assumption.workspace,
        variety=assumption.variety,
        effective_from=effective_from,
        effective_until=remaining,
        germination_rate=(
            assumption.germination_rate if germination_rate is None else germination_rate
        ),
        seeds_per_cluster=(
            assumption.seeds_per_cluster if seeds_per_cluster is None else seeds_per_cluster
        ),
        tray_density=assumption.tray_density if tray_density is None else tray_density,
        notes=notes,
    )
    for stage in existing:
        values = submitted.get(stage.stage_id, {})
        NurseryPlanningStageAssumption.objects.create(
            assumption=revision,
            stage=stage.stage,
            sequence=stage.sequence,
            lead_days=values.get('lead_days', stage.lead_days),
            loss_rate=values.get('loss_rate', stage.loss_rate),
            location=stage.location,
            capacity_basis=stage.capacity_basis,
            capacity_per_plant=stage.capacity_per_plant,
        )
    for row in assumption.inputs.select_related('item').all():
        NurseryPlanningInputAssumption.objects.create(
            assumption=revision, item=row.item,
            quantity_per_plant=row.quantity_per_plant,
        )
    closing = effective_from - timedelta(days=1)
    if assumption.effective_until is None or assumption.effective_until > closing:
        assumption.effective_until = closing
        assumption.save(update_fields=['effective_until'])
    return revision
