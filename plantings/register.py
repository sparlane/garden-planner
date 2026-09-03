"""The nursery plant register: a filtered projection over individual plants.

The register answers inventory-first questions — what is growing, what is
ready, where it is, how old it is, what it cost — without becoming a second
mutable plant table. Every row is derived from the source records, and one
filter parser feeds both the rows and the whole-filter totals so a screen can
never report counts that its own list disagrees with.

Reservation remains with its owning task; health and growth facts are
projected from append-only Nursery observations.
"""

from datetime import timedelta
from typing import NamedTuple

from django.db.models import BooleanField, Case, CharField, Count, DateField, DateTimeField, DecimalField, DurationField, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery, Sum, TextField, Value, When
from django.db.models.functions import Coalesce, Now
from django.contrib.contenttypes.models import ContentType
from rest_framework.exceptions import ValidationError

from costing.models import CostAllocation
from inventory.rest_query import parse_boolean, parse_date, parse_datetime, parse_integer
from locations.models import Location
from labels.models import LabelCode
from health.availability import quarantine_expression, with_quarantine
from sales.models import SalesOrderAllocation, active_allocation_prefetch

from .lifecycle import FINAL_STATES, SELLABLE_STATES, LifecycleState, with_lifecycle_state
from .models import NurseryObservation, SpecificPlant, SpecificPlantLocation


#: Where a plant may currently be, plus the absence of anywhere at all.
LOCATION_TYPES = {
    SpecificPlantLocation.SEED_TRAY_CELL,
    SpecificPlantLocation.GARDEN_SQUARE,
    SpecificPlantLocation.LOCATION,
    SpecificPlantLocation.CONTAINER_UNIT,
}
UNPLACED = 'none'
ALLOCATION_STATUSES = {'none', 'tentative', 'reserved'}

#: The orderings the register offers, each ending in the primary key so that
#: paging through equal sort values cannot repeat or skip a plant.
ORDERINGS = {
    'age': ('germinated', 'pk'),
    'variety': ('variety_name', 'plant_name', 'pk'),
    'location': ('current_location_label', 'pk'),
    'standing_at': ('standing_at_label', 'pk'),
    'cost': ('cost', 'pk'),
    'state': ('lifecycle_state', 'pk'),
    'state_since': ('last_state_at', 'pk'),
    'first_ready': ('first_ready_at', 'pk'),
    'batch': ('batch_code', 'pk'),
    'germinated': ('germinated', 'pk'),
    'expected_ready': ('current_expected_ready', 'pk'),
}
DEFAULT_ORDERING = '-age'

_BATCH = 'batch'

#: A tray stands somewhere as a serialized asset, and the plants in its cells
#: stand there with it.
_TRAY_LOCATION = 'seed_tray_cell__tray__inventory_unit__current_location'

#: A numbered pot carries its own location for the same reason, so a potted
#: specimen is standing wherever its pot was last put down.
_CONTAINER_LOCATION = 'container_unit__current_location'


class RegisterFilters(NamedTuple):
    """One validated set of register filters, shared by rows and totals."""

    variety: object = None
    batch: object = None
    states: tuple = ()
    sellable: object = None
    quarantined: object = None
    reserved: object = None
    allocation_status: object = None
    germinated_from: object = None
    germinated_to: object = None
    location_type: object = None
    seed_tray: object = None
    generation: object = None
    garden_square: object = None
    location: object = None
    stage: object = None
    grade: object = None
    container: object = None
    expected_ready_from: object = None
    expected_ready_to: object = None
    stage_overdue: object = None
    search: str = ''
    ordering: str = DEFAULT_ORDERING


def parse_register_filters(query_params):
    """Validate the query string into filters, blaming each parameter by name."""
    states = tuple(
        state for state in query_params.getlist('state') if state
    )
    valid_states = {state.value for state in LifecycleState}
    unknown = [state for state in states if state not in valid_states]
    if unknown:
        raise ValidationError({'state': 'Select a valid lifecycle state.'})

    location_type = query_params.get('location_type') or None
    if location_type is not None and location_type not in LOCATION_TYPES | {UNPLACED}:
        raise ValidationError({'location_type': 'Select a valid location type.'})

    ordering = query_params.get('ordering') or DEFAULT_ORDERING
    if ordering.lstrip('-') not in ORDERINGS:
        raise ValidationError({'ordering': 'Select a valid ordering.'})

    allocation_status = query_params.get('allocation_status') or None
    if allocation_status is not None and allocation_status not in ALLOCATION_STATUSES:
        raise ValidationError({'allocation_status': 'Select a valid allocation status.'})

    return RegisterFilters(
        variety=parse_integer(query_params.get('variety'), 'variety'),
        batch=parse_integer(query_params.get('batch'), 'batch'),
        states=states,
        sellable=parse_boolean(query_params.get('sellable'), 'sellable'),
        quarantined=parse_boolean(query_params.get('quarantined'), 'quarantined'),
        reserved=parse_boolean(query_params.get('reserved'), 'reserved'),
        allocation_status=allocation_status,
        germinated_from=parse_datetime(query_params.get('germinated_from'), 'germinated_from'),
        germinated_to=parse_datetime(query_params.get('germinated_to'), 'germinated_to'),
        location_type=location_type,
        seed_tray=parse_integer(query_params.get('seed_tray'), 'seed_tray'),
        generation=parse_integer(query_params.get('generation'), 'generation'),
        garden_square=parse_integer(query_params.get('garden_square'), 'garden_square'),
        location=parse_integer(query_params.get('location'), 'location'),
        stage=parse_integer(query_params.get('stage'), 'stage'),
        grade=parse_integer(query_params.get('grade'), 'grade'),
        container=parse_integer(query_params.get('container'), 'container'),
        expected_ready_from=parse_date(query_params.get('expected_ready_from'), 'expected_ready_from'),
        expected_ready_to=parse_date(query_params.get('expected_ready_to'), 'expected_ready_to'),
        stage_overdue=parse_boolean(query_params.get('stage_overdue'), 'stage_overdue'),
        search=(query_params.get('search') or '').strip(),
        ordering=ordering,
    )


def _current_location(field):
    """Read one field of the location a plant currently occupies.

    `unique_active_location_per_plant` guarantees there is at most one, so this
    is a lookup rather than a choice between candidates.
    """
    return Subquery(
        SpecificPlantLocation.objects
        .filter(specific_plant=OuterRef('pk'), ended__isnull=True)
        .values(field)[:1]
    )


def _plant_cost():
    """Sum what one plant's surviving cost layers came to.

    A subquery rather than an aggregate over the join, so that adding it cannot
    multiply the other annotations. The row selection matches the one
    `costing.services.plant_cost_breakdown` reports from: a reversal and the
    layer it reverses both drop out.
    """
    return Subquery(
        CostAllocation.objects
        .filter(
            specific_plant=OuterRef('pk'),
            reversal_of__isnull=True,
            reversal__isnull=True,
        )
        .values('specific_plant')
        .annotate(total=Sum('amount'))
        .values('total')[:1],
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )


def _current_observation(field, output_field=None, observed_field=None):
    """Read the newest effective observation that supplied one field."""
    queryset = (
        NurseryObservation.objects
        .filter(
            targets__plant=OuterRef('pk'),
            correction__isnull=True,
        )
        .exclude(**{f'{observed_field or field}__isnull': True})
        .order_by('-occurred_at', '-pk')
        .values(field)[:1]
    )
    return Subquery(queryset, output_field=output_field)


def register_projection(workspace):
    """Return every plant in the workspace with its register columns."""
    plant_content_type = ContentType.objects.get_for_model(SpecificPlant)
    active_label = LabelCode.objects.filter(
        workspace=workspace,
        status=LabelCode.Status.ACTIVE,
        identity__target_content_type=plant_content_type,
        identity__target_object_id=OuterRef('pk'),
    ).values('code')[:1]
    queryset = with_lifecycle_state(
        SpecificPlant.objects
        .filter(workspace=workspace)
        .select_related(f'{_BATCH}__variety__plant')
    )
    queryset = with_quarantine(queryset)
    queryset = queryset.annotate(
        reserved=Exists(
            SalesOrderAllocation.objects.filter(
                plant_id=OuterRef('pk'),
                status=SalesOrderAllocation.Status.RESERVED,
            ),
        ),
        # When the current hold lapses, so an operator filtering on reserved
        # can tell a hold that frees itself tonight from one with no end at
        # all. A plant carries at most one reserved allocation — the unique
        # constraint on `sales_one_active_plant_reservation` — so this is the
        # hold's own expiry rather than a choice between several.
        reserved_until=Subquery(
            SalesOrderAllocation.objects.filter(
                plant_id=OuterRef('pk'),
                status=SalesOrderAllocation.Status.RESERVED,
            ).values('expires_at')[:1],
            output_field=DateTimeField(),
        ),
        tentative=Exists(
            SalesOrderAllocation.objects.filter(
                plant_id=OuterRef('pk'),
                status=SalesOrderAllocation.Status.PENDING,
            ),
        ),
    )
    return queryset.annotate(
        allocation_status=Case(
            When(reserved=True, then=Value('reserved')),
            When(tentative=True, then=Value('tentative')),
            default=Value('none'),
            output_field=CharField(),
        ),
        sellable=Case(
            When(
                ~quarantine_expression(),
                reserved=False,
                lifecycle_state__in=sorted(SELLABLE_STATES),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
        batch_code=F(f'{_BATCH}__code'),
        variety_name=F(f'{_BATCH}__variety__name'),
        plant_name=F(f'{_BATCH}__variety__plant__name'),
        current_location_type=_current_location('location_type'),
        current_seed_tray_cell=_current_location('seed_tray_cell'),
        current_seed_tray=_current_location('seed_tray_cell__tray'),
        current_seed_tray_label=_current_location('seed_tray_cell__tray__model__identifier'),
        current_garden_square=_current_location('garden_square'),
        current_garden_square_label=_current_location('garden_square__name'),
        current_container_unit=_current_location('container_unit'),
        current_container_unit_label=_current_location('container_unit__asset_code'),
        located_since=_current_location('started'),
        cost=_plant_cost(),
        label_code=Subquery(active_label),
        direct_location=_current_location('location'),
        direct_location_name=_current_location('location__name'),
        direct_location_path=_current_location('location__path'),
        tray_location=_current_location(_TRAY_LOCATION),
        tray_location_name=_current_location(f'{_TRAY_LOCATION}__name'),
        tray_location_path=_current_location(f'{_TRAY_LOCATION}__path'),
        container_location=_current_location(_CONTAINER_LOCATION),
        container_location_name=_current_location(f'{_CONTAINER_LOCATION}__name'),
        container_location_path=_current_location(f'{_CONTAINER_LOCATION}__path'),
        current_stage=_current_observation('stage_id', IntegerField()),
        current_stage_name=_current_observation('stage__name', TextField()),
        current_stage_target_days=_current_observation('stage__target_days', IntegerField(), 'stage'),
        current_stage_observed_at=_current_observation('occurred_at', DateTimeField(), 'stage'),
        current_grade=_current_observation('grade_id', IntegerField()),
        current_grade_name=_current_observation('grade__name', TextField()),
        current_container=_current_observation('container_item_id', IntegerField()),
        current_container_name=_current_observation('container_name', TextField(), 'container_item'),
        current_container_size=_current_observation('container_size_label', TextField(), 'container_item'),
        current_container_count=_current_observation('container_count', IntegerField(), 'container_item'),
        current_expected_ready=_current_observation('expected_ready', DateField()),
    ).annotate(
        current_location_label=Coalesce(
            'current_garden_square_label',
            'current_seed_tray_label',
            'direct_location_name',
            'current_container_unit_label',
            Value(''),
            output_field=TextField(),
        ),
        # Where the plant is physically standing, which for a plant in a tray
        # is wherever the tray has been wheeled and for a potted specimen is
        # wherever its pot was last put down. Both assets carry that placement
        # themselves, so it is resolved here rather than copied onto every
        # plant they carry.
        standing_at=Coalesce(
            'direct_location', 'tray_location', 'container_location',
        ),
        standing_at_label=Coalesce(
            'direct_location_name',
            'tray_location_name',
            'container_location_name',
            Value(''),
            output_field=TextField(),
        ),
        standing_at_path=Coalesce(
            'direct_location_path',
            'tray_location_path',
            'container_location_path',
            Value(''),
            output_field=TextField(),
        ),
        stage_due_at=ExpressionWrapper(
            F('current_stage_observed_at') + ExpressionWrapper(
                F('current_stage_target_days') * Value(timedelta(days=1)),
                output_field=DurationField(),
            ),
            output_field=DateTimeField(),
        ),
    ).prefetch_related(active_allocation_prefetch())


def _apply_search(queryset, search):
    """Match a typed identifier or a name fragment the operator remembers.

    A search that is only digits is read as an identifier and matched against
    plant IDs and batch codes alone. Someone holding a plant and typing its
    number wants that plant, not every crop whose catalog name happens to
    contain the same digit. Label codes join this once task 18 issues them.
    """
    matches = Q(batch_code__icontains=search) | Q(label_code__iexact=search)
    if search.isdigit():
        matches |= Q(pk=int(search))
    else:
        matches |= Q(variety_name__icontains=search) | Q(plant_name__icontains=search)
    return queryset.filter(matches)


def register_queryset(workspace, filters):  # pylint: disable=too-many-branches
    """Return the plants one validated filter set selects, in its order."""
    queryset = register_projection(workspace)
    if filters.variety is not None:
        queryset = queryset.filter(**{f'{_BATCH}__variety_id': filters.variety})
    if filters.batch is not None:
        queryset = queryset.filter(**{f'{_BATCH}_id': filters.batch})
    if filters.states:
        queryset = queryset.filter(lifecycle_state__in=list(filters.states))
    if filters.sellable is not None:
        queryset = queryset.filter(sellable=filters.sellable)
    if filters.quarantined is not None:
        queryset = queryset.filter(quarantined=filters.quarantined)
    if filters.reserved is not None:
        queryset = queryset.filter(reserved=filters.reserved)
    if filters.allocation_status is not None:
        queryset = queryset.filter(allocation_status=filters.allocation_status)
    if filters.germinated_from is not None:
        queryset = queryset.filter(germinated__gte=filters.germinated_from)
    if filters.germinated_to is not None:
        queryset = queryset.filter(germinated__lte=filters.germinated_to)
    if filters.location_type == UNPLACED:
        queryset = queryset.filter(current_location_type__isnull=True)
    elif filters.location_type is not None:
        queryset = queryset.filter(current_location_type=filters.location_type)
    if filters.seed_tray is not None:
        queryset = queryset.filter(current_seed_tray=filters.seed_tray)
    if filters.generation is not None:
        queryset = queryset.filter(
            cell_planting__seed_tray_planting__generation_id=filters.generation,
        )
    if filters.garden_square is not None:
        queryset = queryset.filter(current_garden_square=filters.garden_square)
    if filters.location is not None:
        queryset = _standing_in(queryset, workspace, filters.location)
    if filters.stage is not None:
        queryset = queryset.filter(current_stage=filters.stage)
    if filters.grade is not None:
        queryset = queryset.filter(current_grade=filters.grade)
    if filters.container is not None:
        queryset = queryset.filter(current_container=filters.container)
    if filters.expected_ready_from is not None:
        queryset = queryset.filter(current_expected_ready__gte=filters.expected_ready_from)
    if filters.expected_ready_to is not None:
        queryset = queryset.filter(current_expected_ready__lte=filters.expected_ready_to)
    if filters.stage_overdue is True:
        queryset = queryset.filter(stage_due_at__lt=Now())
    elif filters.stage_overdue is False:
        queryset = queryset.filter(Q(stage_due_at__gte=Now()) | Q(stage_due_at__isnull=True))
    if filters.search:
        queryset = _apply_search(queryset, filters.search)
    return queryset.order_by(*_ordering_fields(filters.ordering))


def _standing_in(queryset, workspace, location_id):
    """Select the plants standing at a location or anywhere below it.

    Asking about a greenhouse means asking about its benches and their bays;
    an operator standing in the doorway does not think of those as elsewhere.
    Unknown ids match nothing rather than everything, because an empty path
    prefix would select the whole register.
    """
    path = (
        Location.objects
        .filter(pk=location_id, workspace=workspace)
        .values_list('path', flat=True)
        .first()
    )
    if not path:
        return queryset.none()
    return queryset.filter(standing_at_path__startswith=path)


def _ordering_fields(ordering):
    """Expand one ordering name into its fields, keeping the tiebreaker last."""
    descending = ordering.startswith('-')
    fields = ORDERINGS[ordering.lstrip('-')]
    if not descending:
        return fields
    return tuple(f'-{field}' for field in fields[:-1]) + fields[-1:]


def register_totals(queryset):
    """Count the whole filtered selection, independent of any page of it.

    Growing and available are reported separately rather than as one present
    count, unresolved names the plants still owed an outcome, and quarantined
    names the matching plants constrained by an active health case. Reserved is
    absent until task 44 records it.
    """
    totals = {
        'total': 0,
        'unresolved': 0,
        'quarantined': 0,
        'reserved': 0,
        'tentative': 0,
        **{state.value: 0 for state in LifecycleState},
    }
    core_rows = queryset.order_by().values(
        'lifecycle_state', 'quarantined', 'reserved', 'allocation_status',
    ).annotate(count=Count('pk'))
    for row in core_rows:
        count = row['count']
        state = row['lifecycle_state']
        totals['total'] += count
        if state not in FINAL_STATES:
            totals['unresolved'] += count
        if row['quarantined']:
            totals['quarantined'] += count
        if row['reserved']:
            totals['reserved'] += count
        if row['allocation_status'] == 'tentative':
            totals['tentative'] += count
        if state != LifecycleState.AVAILABLE or (
            not row['quarantined'] and not row['reserved']
        ):
            totals[state] += count
    totals['stage_counts'] = {
        str(row['current_stage']): row['count']
        for row in queryset.order_by().values('current_stage').annotate(count=Count('pk'))
        if row['current_stage'] is not None
    }
    totals['grade_counts'] = {
        str(row['current_grade']): row['count']
        for row in queryset.order_by().values('current_grade').annotate(count=Count('pk'))
        if row['current_grade'] is not None
    }
    totals['container_counts'] = {
        str(row['current_container']): row['count']
        for row in queryset.order_by().values('current_container').annotate(count=Count('pk'))
        if row['current_container'] is not None
    }
    return totals
