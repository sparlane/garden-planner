"""Calculation, approval, revision, and variance for nursery production plans."""

# The issue and demand calculators keep their named inputs visible at each call.
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals

from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from inventory.ledger import unit_is_in_use, unit_physical_state
from inventory.models import StockMovement
from locations.occupancy import location_occupancy
from seeds.models import Seeds
from seedtrays.models import SeedTray

from .batches import BatchRequest, batch_seeds_sown, create_batch
from .models import (
    NurseryPlanDemand,
    NurseryPlanInputRequirement,
    NurseryPlanIssue,
    NurseryPlanMilestone,
    NurseryPlanRequirement,
    NurseryPlanningAssumption,
    NurseryProductionPlan,
    PlantCohort,
)


def _ceil(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def _actor(user):
    return user if user is not None and user.is_authenticated else None


def effective_assumption(workspace, variety, on_date):
    """Return the latest assumption whose explicit effective range covers a date."""
    return (
        NurseryPlanningAssumption.objects
        .filter(
            workspace=workspace,
            variety=variety,
            effective_from__lte=on_date,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=on_date))
        .prefetch_related('stages__stage', 'stages__location', 'inputs__item')
        .order_by('-effective_from', '-pk')
        .first()
    )


def _item_balance(workspace, item):
    totals = StockMovement.objects.filter(
        workspace=workspace, lot__item=item,
    ).aggregate(
        incoming=Sum('quantity', filter=Q(destination__isnull=False)),
        outgoing=Sum('quantity', filter=Q(source__isnull=False)),
    )
    return (totals['incoming'] or Decimal('0')) - (totals['outgoing'] or Decimal('0'))


def _seed_balance(workspace, variety):
    item_ids = Seeds.objects.filter(
        workspace=workspace, plant_variety=variety, inventory_item__isnull=False,
    ).values_list('inventory_item_id', flat=True)
    totals = StockMovement.objects.filter(
        workspace=workspace, lot__item_id__in=item_ids,
    ).aggregate(
        incoming=Sum('quantity', filter=Q(destination__isnull=False)),
        outgoing=Sum('quantity', filter=Q(source__isnull=False)),
    )
    return (totals['incoming'] or Decimal('0')) - (totals['outgoing'] or Decimal('0'))


def _stage_math(target, stages):
    """Work backward through losses and return input/output by stage sequence."""
    output = target
    rows = {}
    for stage in reversed(stages):
        input_quantity = _ceil(Decimal(output) / (Decimal('1') - stage.loss_rate))
        rows[stage.pk] = (input_quantity, output)
        output = input_quantity
    return rows, output


def _schedule(plan, demand, stages):
    total_days = sum(stage.lead_days for stage in stages)
    if plan.direction == NurseryProductionPlan.Direction.BACKWARD:
        sowing_date = demand.ready_from - timedelta(days=total_days)
    else:
        sowing_date = plan.sowing_date
    elapsed = 0
    dates = {}
    for stage in stages:
        dates[stage.pk] = sowing_date + timedelta(days=elapsed)
        elapsed += stage.lead_days
    return sowing_date, dates, sowing_date + timedelta(days=total_days)


def _snapshot(assumption, stages):
    return {
        'assumption_id': assumption.pk,
        'effective_from': assumption.effective_from.isoformat(),
        'effective_until': (
            assumption.effective_until.isoformat() if assumption.effective_until else None
        ),
        'germination_rate': str(assumption.germination_rate),
        'seeds_per_cluster': assumption.seeds_per_cluster,
        'tray_density': assumption.tray_density,
        'stages': [
            {
                'stage_id': row.stage_id,
                'stage_code': row.stage.code,
                'sequence': row.sequence,
                'lead_days': row.lead_days,
                'loss_rate': str(row.loss_rate),
                'location_id': row.location_id,
                'capacity_basis': row.capacity_basis,
                'capacity_per_plant': str(row.capacity_per_plant),
            }
            for row in stages
        ],
    }


def _add_issue(plan, demand, kind, message, required=None, available=None):
    NurseryPlanIssue.objects.create(
        plan=plan,
        demand=demand,
        kind=kind,
        message=message,
        required_quantity=required,
        available_quantity=available,
    )


def _stock_issues(plan, demand, requirement, assumption):
    seed_available = _seed_balance(plan.workspace, demand.variety)
    if seed_available < requirement.required_seeds:
        _add_issue(
            plan, demand, NurseryPlanIssue.Kind.SEED,
            f'Only {seed_available} seeds are recorded as available.',
            requirement.required_seeds, seed_available,
        )
    for row in assumption.inputs.all():
        quantity = Decimal(requirement.required_clusters) * row.quantity_per_plant
        NurseryPlanInputRequirement.objects.create(
            requirement=requirement, item=row.item, quantity=quantity,
        )
        available = _item_balance(plan.workspace, row.item)
        if available < quantity:
            _add_issue(
                plan, demand, NurseryPlanIssue.Kind.INPUT,
                f'Only {available} {row.item.base_unit} of {row.item.name} is available.',
                quantity, available,
            )


def _tray_issue(plan, demand, requirement):
    trays = SeedTray.objects.filter(
        workspace=plan.workspace,
    ).select_related('inventory_unit__current_location')
    available = sum(
        1 for tray in trays
        if all((
            unit_physical_state(tray.inventory_unit) == 'available',
            not unit_is_in_use(tray.inventory_unit),
        ))
    )
    other_planned = NurseryPlanRequirement.objects.filter(
        demand__plan__workspace=plan.workspace,
        demand__plan__status=NurseryProductionPlan.Status.APPROVED,
        sowing_date=requirement.sowing_date,
    ).exclude(demand__plan=plan).aggregate(total=Sum('required_trays'))['total'] or 0
    available -= other_planned
    if available < requirement.required_trays:
        _add_issue(
            plan, demand, NurseryPlanIssue.Kind.TRAY,
            f'Only {max(available, 0)} unallocated trays are available on the sowing date.',
            requirement.required_trays, max(available, 0),
        )


def _capacity_issue(plan, demand, milestone):
    if milestone.location is None or milestone.location.capacity_value is None:
        return
    basis = milestone.capacity_basis
    occupied = location_occupancy(milestone.location, subtree=True).of(basis)
    planned = NurseryPlanMilestone.objects.filter(
        requirement__demand__plan__workspace=plan.workspace,
        requirement__demand__plan__status=NurseryProductionPlan.Status.APPROVED,
        location=milestone.location,
        planned_date=milestone.planned_date,
        capacity_basis=basis,
    ).exclude(requirement__demand__plan=plan).aggregate(
        total=Sum('capacity_required'),
    )['total'] or Decimal('0')
    available = milestone.location.capacity_value - Decimal(occupied) - planned
    if available < milestone.capacity_required:
        _add_issue(
            plan, demand, NurseryPlanIssue.Kind.CAPACITY,
            f'{milestone.location.name} has insufficient {basis} capacity.',
            milestone.capacity_required, max(available, Decimal('0')),
        )


def _calculate_demand(plan, demand, assumption):
    stages = list(assumption.stages.all().order_by('sequence', 'pk'))
    stage_rows, stage_input = _stage_math(demand.target_quantity, stages)
    required_clusters = _ceil(Decimal(stage_input) / assumption.germination_rate)
    required_seeds = required_clusters * assumption.seeds_per_cluster
    required_trays = _ceil(Decimal(required_clusters) / assumption.tray_density)
    sowing_date, dates, ready_date = _schedule(plan, demand, stages)
    requirement = NurseryPlanRequirement.objects.create(
        demand=demand,
        assumption=assumption,
        required_seeds=required_seeds,
        required_clusters=required_clusters,
        required_trays=required_trays,
        expected_finished=demand.target_quantity,
        sowing_date=sowing_date,
        expected_ready_from=ready_date,
        expected_ready_until=ready_date + (demand.ready_until - demand.ready_from),
        assumption_snapshot=_snapshot(assumption, stages),
    )
    for row in stages:
        input_quantity, output = stage_rows[row.pk]
        milestone = NurseryPlanMilestone.objects.create(
            requirement=requirement,
            stage=row.stage,
            sequence=row.sequence,
            planned_date=dates[row.pk],
            input_quantity=input_quantity,
            expected_output=output,
            location=row.location,
            capacity_basis=row.capacity_basis,
            capacity_required=Decimal(input_quantity) * row.capacity_per_plant,
        )
        _capacity_issue(plan, demand, milestone)
    _stock_issues(plan, demand, requirement, assumption)
    _tray_issue(plan, demand, requirement)
    return requirement


@transaction.atomic
def calculate_plan(plan):
    """Replace a draft's projections and return its current requirements."""
    plan = NurseryProductionPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != NurseryProductionPlan.Status.DRAFT:
        raise ValidationError({'status': 'Approved plans cannot be recalculated.'})
    NurseryPlanIssue.objects.filter(plan=plan).delete()
    NurseryPlanRequirement.objects.filter(demand__plan=plan).delete()
    for demand in plan.demand_lines.select_related('variety').order_by('pk'):
        assumption = effective_assumption(plan.workspace, demand.variety, demand.ready_from)
        if assumption is None:
            _add_issue(
                plan, demand, NurseryPlanIssue.Kind.ASSUMPTION,
                f'No effective planning assumption exists for {demand.variety}.',
            )
            continue
        _calculate_demand(plan, demand, assumption)
    return NurseryPlanRequirement.objects.filter(
        demand__plan=plan,
    ).select_related('demand', 'assumption')


@transaction.atomic
def approve_plan(plan, user):
    """Freeze a calculated version and create linked planned batches only."""
    plan = NurseryProductionPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != NurseryProductionPlan.Status.DRAFT:
        raise ValidationError({'status': 'Only a draft plan can be approved.'})
    demands = list(plan.demand_lines.order_by('pk'))
    requirements = list(NurseryPlanRequirement.objects.filter(demand__plan=plan).order_by('pk'))
    if not demands or len(requirements) != len(demands):
        raise ValidationError({'requirements': 'Calculate every demand line before approval.'})
    for requirement in requirements:
        batch = create_batch(
            plan.workspace,
            user,
            BatchRequest(
                code=f'{plan.code}-V{plan.version}-D{requirement.demand_id}',
                variety=requirement.demand.variety,
                planned_start=requirement.sowing_date,
                notes=f'Created from approved nursery plan {plan}.',
            ),
        )
        requirement.batch = batch
        requirement.save(update_fields=['batch'])
    plan.status = NurseryProductionPlan.Status.APPROVED
    plan.approved_at = timezone.now()
    plan.approved_by = _actor(user)
    plan.save()
    return plan


@transaction.atomic
def revise_plan(plan, user):
    """Clone demand into a new draft without changing the approved version."""
    plan = NurseryProductionPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != NurseryProductionPlan.Status.APPROVED:
        raise ValidationError({'status': 'Only an approved plan can be revised.'})
    versions = NurseryProductionPlan.objects.select_for_update().filter(
        workspace=plan.workspace, code=plan.code,
    ).order_by('-version')
    latest = versions.values_list('version', flat=True).first()
    revision = NurseryProductionPlan.objects.create(
        workspace=plan.workspace,
        code=plan.code,
        version=latest + 1,
        direction=plan.direction,
        sowing_date=plan.sowing_date,
        supersedes=plan,
        notes=plan.notes,
        created_by=_actor(user),
    )
    for demand in plan.demand_lines.order_by('pk'):
        NurseryPlanDemand.objects.create(
            plan=revision,
            variety=demand.variety,
            product_reference=demand.product_reference,
            target_quantity=demand.target_quantity,
            ready_from=demand.ready_from,
            ready_until=demand.ready_until,
            source=demand.source,
            priority=demand.priority,
            customer_reference=demand.customer_reference,
            order_reference=demand.order_reference,
            source_line_reference=demand.source_line_reference,
            notes=demand.notes,
        )
    return revision


def plan_variance(plan):
    """Reconcile immutable requirements with current batch and cohort facts."""
    rows = []
    for requirement in NurseryPlanRequirement.objects.filter(
            demand__plan=plan,
    ).select_related('demand', 'batch').order_by('pk'):
        batch = requirement.batch
        seeds_sown = batch_seeds_sown(batch) if batch else 0
        current_output = 0
        if batch:
            current_output = PlantCohort.objects.filter(batch=batch).aggregate(
                total=Sum('quantity'),
            )['total'] or 0
        rows.append({
            'demand': requirement.demand_id,
            'batch': batch.pk if batch else None,
            'planned_sowing_date': requirement.sowing_date,
            'actual_sowing_date': batch.actual_start.date() if batch and batch.actual_start else None,
            'planned_seeds': requirement.required_seeds,
            'actual_seeds': seeds_sown,
            'seed_variance': seeds_sown - requirement.required_seeds,
            'planned_output': requirement.expected_finished,
            'current_output': current_output,
            'output_variance': current_output - requirement.expected_finished,
            'batch_status': batch.status if batch else None,
        })
    return rows
