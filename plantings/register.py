"""The nursery plant register: a filtered projection over individual plants.

The register answers inventory-first questions — what is growing, what is
ready, where it is, how old it is, what it cost — without becoming a second
mutable plant table. Every row is derived from the source records, and one
filter parser feeds both the rows and the whole-filter totals so a screen can
never report counts that its own list disagrees with.

Filters for growth stage, grade, container, reservation, and quarantine are
deliberately absent: nothing records those facts yet, and tasks 54, 44, and 56
each add their filter here when they add their model.
"""

from typing import NamedTuple

from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum, TextField, Value
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from costing.models import CostAllocation
from inventory.rest_query import parse_boolean, parse_datetime, parse_integer
from locations.models import Location

from .lifecycle import FINAL_STATES, LifecycleState, with_lifecycle_state
from .models import SpecificPlant, SpecificPlantLocation


#: Where a plant may currently be, plus the absence of anywhere at all.
LOCATION_TYPES = {
    SpecificPlantLocation.SEED_TRAY_CELL,
    SpecificPlantLocation.GARDEN_SQUARE,
    SpecificPlantLocation.LOCATION,
}
UNPLACED = 'none'

#: The orderings the register offers, each ending in the primary key so that
#: paging through equal sort values cannot repeat or skip a plant.
ORDERINGS = {
    'age': ('germinated', 'pk'),
    'variety': ('variety_name', 'plant_name', 'pk'),
    'location': ('current_location_label', 'pk'),
    'standing_at': ('standing_at_label', 'pk'),
    'cost': ('cost', 'pk'),
    'state': ('lifecycle_state', 'pk'),
    'batch': ('batch_code', 'pk'),
    'germinated': ('germinated', 'pk'),
}
DEFAULT_ORDERING = '-age'

_BATCH = 'cell_planting__seed_tray_planting__batch'

#: A tray stands somewhere as a serialized asset, and the plants in its cells
#: stand there with it.
_TRAY_LOCATION = 'seed_tray_cell__tray__inventory_unit__current_location'


class RegisterFilters(NamedTuple):
    """One validated set of register filters, shared by rows and totals."""

    variety: object = None
    batch: object = None
    states: tuple = ()
    sellable: object = None
    germinated_from: object = None
    germinated_to: object = None
    location_type: object = None
    seed_tray: object = None
    garden_square: object = None
    location: object = None
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

    return RegisterFilters(
        variety=parse_integer(query_params.get('variety'), 'variety'),
        batch=parse_integer(query_params.get('batch'), 'batch'),
        states=states,
        sellable=parse_boolean(query_params.get('sellable'), 'sellable'),
        germinated_from=parse_datetime(query_params.get('germinated_from'), 'germinated_from'),
        germinated_to=parse_datetime(query_params.get('germinated_to'), 'germinated_to'),
        location_type=location_type,
        seed_tray=parse_integer(query_params.get('seed_tray'), 'seed_tray'),
        garden_square=parse_integer(query_params.get('garden_square'), 'garden_square'),
        location=parse_integer(query_params.get('location'), 'location'),
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


def register_projection(workspace):
    """Return every plant in the workspace with its register columns."""
    return with_lifecycle_state(
        SpecificPlant.objects
        .filter(workspace=workspace)
        .select_related(f'{_BATCH}__variety__plant')
    ).annotate(
        batch_code=F(f'{_BATCH}__code'),
        variety_name=F(f'{_BATCH}__variety__name'),
        plant_name=F(f'{_BATCH}__variety__plant__name'),
        current_location_type=_current_location('location_type'),
        current_seed_tray_cell=_current_location('seed_tray_cell'),
        current_seed_tray=_current_location('seed_tray_cell__tray'),
        current_seed_tray_label=_current_location('seed_tray_cell__tray__model__identifier'),
        current_garden_square=_current_location('garden_square'),
        current_garden_square_label=_current_location('garden_square__name'),
        located_since=_current_location('started'),
        cost=_plant_cost(),
        direct_location=_current_location('location'),
        direct_location_name=_current_location('location__name'),
        direct_location_path=_current_location('location__path'),
        tray_location=_current_location(_TRAY_LOCATION),
        tray_location_name=_current_location(f'{_TRAY_LOCATION}__name'),
        tray_location_path=_current_location(f'{_TRAY_LOCATION}__path'),
    ).annotate(
        current_location_label=Coalesce(
            'current_garden_square_label',
            'current_seed_tray_label',
            'direct_location_name',
            Value(''),
            output_field=TextField(),
        ),
        # Where the plant is physically standing, which for a plant in a tray
        # is wherever the tray has been wheeled. The tray's placement is the
        # only record of that, so it is resolved here rather than copied onto
        # every plant it carries.
        standing_at=Coalesce('direct_location', 'tray_location'),
        standing_at_label=Coalesce(
            'direct_location_name',
            'tray_location_name',
            Value(''),
            output_field=TextField(),
        ),
        standing_at_path=Coalesce(
            'direct_location_path',
            'tray_location_path',
            Value(''),
            output_field=TextField(),
        ),
    )


def _apply_search(queryset, search):
    """Match a typed identifier or a name fragment the operator remembers.

    A search that is only digits is read as an identifier and matched against
    plant IDs and batch codes alone. Someone holding a plant and typing its
    number wants that plant, not every crop whose catalog name happens to
    contain the same digit. Label codes join this once task 18 issues them.
    """
    matches = Q(batch_code__icontains=search)
    if search.isdigit():
        matches |= Q(pk=int(search))
    else:
        matches |= Q(variety_name__icontains=search) | Q(plant_name__icontains=search)
    return queryset.filter(matches)


def register_queryset(workspace, filters):
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
    if filters.garden_square is not None:
        queryset = queryset.filter(current_garden_square=filters.garden_square)
    if filters.location is not None:
        queryset = _standing_in(queryset, workspace, filters.location)
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
    count, and unresolved names the plants still owed an outcome. Reserved and
    quarantined are absent until tasks 44 and 56 record them.
    """
    counted = {'total': Count('pk')}
    for state in LifecycleState:
        counted[state.value] = Count('pk', filter=Q(lifecycle_state=state.value))
    counted['unresolved'] = Count(
        'pk',
        filter=~Q(lifecycle_state__in=sorted(FINAL_STATES)),
    )
    return queryset.order_by().aggregate(**counted)
