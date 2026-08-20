"""
Planting views
"""

import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, Sum
from django.http import HttpResponseNotAllowed, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404

from gp.utils import get_request_data
from plants.metadata import variety_days
from plants.models import MaturityBasis
from workspaces.models import get_current_workspace
from .models import GardenPlanting, SeedTrayPlanting, GardenSquareDirectSowPlanting, GardenSquareTransplant, SpecificPlant, SpecificPlantLocation


@login_required
def seedtray_current(request):
    """
    List the seedtray plantings that are currently growing
    """
    workspace = get_current_workspace()
    plantings = (
        SeedTrayPlanting.objects
        .filter(removed=False, workspace=workspace)
        .order_by('planted')
        .select_related('seeds_used__seeds__plant_variety__plant', 'seed_tray', 'batch')
        .prefetch_related('cell_plantings__cell')
    )
    garden_square_location_counts = dict(
        SpecificPlantLocation.objects.filter(
            specific_plant__workspace=workspace,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            specific_plant__cell_planting__seed_tray_planting__in=plantings,
        )
        .values_list('specific_plant__cell_planting__seed_tray_planting_id')
        .annotate(total=Count('specific_plant_id', distinct=True))
    )
    germinated_counts = dict(
        SpecificPlant.objects
        .filter(
            workspace=workspace,
            cell_planting__seed_tray_planting__in=plantings,
        )
        .values('cell_planting__seed_tray_planting')
        .annotate(total=Count('id'))
        .values_list('cell_planting__seed_tray_planting', 'total')
    )
    transplanted_counts = dict(
        GardenSquareTransplant.objects
        .filter(workspace=workspace, original_planting__in=plantings)
        .values('original_planting')
        .annotate(total=Sum('quantity'))
        .values_list('original_planting', 'total')
    )
    planting_data = []
    for planting in plantings:
        if planting.pk in garden_square_location_counts:
            transplanted_count = garden_square_location_counts[planting.pk]
        else:
            transplanted_count = transplanted_counts.get(planting.pk, 0)
        germinated_count = germinated_counts.get(planting.pk, 0)
        variety = planting.seeds_used.seeds.plant_variety
        germination_min, germination_max, _, _ = _get_variety_days(variety)
        planting_data.append({
            'pk': planting.pk,
            'seeds_used': planting.seeds_used_id,
            'batch': planting.batch_id,
            'batch_code': planting.batch.code,
            'plant': variety.plant.name,
            'variety': variety.name,
            'planted': planting.planted,
            'quantity': planting.quantity,
            'seed_tray': planting.seed_tray.pk if planting.seed_tray else None,
            'location': planting.location,
            'notes': planting.notes,
            'germination_date_early': _add_nullable_days(planting.planted, germination_min),
            'germination_date_late': _add_nullable_days(planting.planted, germination_max),
            'germinated_count': germinated_count,
            'transplanted_count': transplanted_count,
            'cell_plantings': [
                {'pk': cp.pk, 'cell': cp.cell.pk, 'quantity': cp.quantity}
                for cp in planting.cell_plantings.all()
            ],
        })
    return JsonResponse({'plantings': planting_data})


@login_required
def seedtray_complete(request):
    """
    Complete/Remove the remaining contents of a seed tray
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    data = get_request_data(request)

    planting = get_object_or_404(
        SeedTrayPlanting,
        pk=data.get('planting'),
        workspace=get_current_workspace(),
    )
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)


def _get_variety_days(variety):
    return (*variety_days(variety, 'germination'), *variety_days(variety, 'maturity'))


def _maturity_anchor(variety, sowed_at, transplanted_at=None):
    """Choose the recorded event configured as the start of maturity."""
    if variety.effective_maturity_basis == MaturityBasis.TRANSPLANTING:
        return transplanted_at
    return sowed_at


def _add_nullable_days(value, days):
    """
    Add a nullable day offset to a date/datetime value.
    """
    if days is None:
        return None
    return value + datetime.timedelta(days=days)


@login_required
def gardensquare_current(request):  # pylint: disable=too-many-locals
    """
    List the GardenSquare plantings that are currently growing
    """
    workspace = get_current_workspace()
    plantings = (
        GardenSquareDirectSowPlanting.objects
        .filter(removed=False, workspace=workspace)
        .order_by('planted')
        .select_related('seeds_used__seeds__plant_variety__plant', 'location__bed__area', 'batch')
    )
    planting_data = []
    for planting in plantings:
        variety = planting.seeds_used.seeds.plant_variety
        germination_min, germination_max, maturity_min, maturity_max = _get_variety_days(variety)
        planting_data.append({
            'planting_pk': planting.pk,
            'seeds_used': planting.seeds_used_id,
            'batch': planting.batch_id,
            'batch_code': planting.batch.code,
            'plant': planting.seeds_used.seeds.plant_variety.plant.name,
            'variety': planting.seeds_used.seeds.plant_variety.name,
            'planted': planting.planted,
            'quantity': planting.quantity,
            'location': planting.location.as_json(),
            'notes': planting.notes,
            'germination_date_early': _add_nullable_days(planting.planted, germination_min),
            'germination_date_late': _add_nullable_days(planting.planted, germination_max),
            'maturity_date_early': _add_nullable_days(planting.planted, maturity_min),
            'maturity_date_late': _add_nullable_days(planting.planted, maturity_max)
        })
    quick_aggregates = GardenPlanting.objects.filter(
        workspace=workspace,
        tracking=GardenPlanting.Tracking.AGGREGATE,
        garden_square__isnull=False,
        finished_on__isnull=True,
    ).select_related('batch__variety__plant', 'garden_square__bed__area')
    for planting in quick_aggregates:
        variety = planting.batch.variety
        _, _, maturity_min, maturity_max = _get_variety_days(variety)
        planting_data.append({
            'garden_planting_pk': planting.pk,
            'planting_pk': planting.pk,
            'batch': planting.batch_id,
            'batch_code': planting.batch.code,
            'plant': variety.plant.name,
            'variety': variety.name,
            'planted': planting.recorded_on,
            'quantity': planting.quantity,
            'quantity_is_approximate': planting.quantity_is_approximate,
            'date_is_approximate': planting.date_is_approximate,
            'source': planting.source,
            'perennial': planting.perennial,
            'location': planting.garden_square.as_json(),
            'notes': planting.notes,
            'maturity_date_early': _add_nullable_days(planting.recorded_on, maturity_min),
            'maturity_date_late': _add_nullable_days(planting.recorded_on, maturity_max),
        })
    specific_garden_locations = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=workspace,
        location_type=SpecificPlantLocation.GARDEN_SQUARE,
        specific_plant__cell_planting__seed_tray_planting_id=OuterRef(
            'original_planting_id'
        ),
    )
    transplantings = (
        GardenSquareTransplant.objects
        .filter(removed=False, workspace=workspace)
        .annotate(
            has_specific_representation=Exists(specific_garden_locations),
        )
        .filter(has_specific_representation=False)
        .select_related(
            'original_planting__seeds_used__seeds__plant_variety__plant',
            'original_planting__batch',
            'location__bed__area',
        )
    )
    for transplanting in transplantings:
        planting = transplanting.original_planting
        variety = planting.seeds_used.seeds.plant_variety
        germination_min, germination_max, maturity_min, maturity_max = _get_variety_days(variety)
        planting_data.append({
            'transplanting_pk': transplanting.pk,
            'planting_pk': planting.pk,
            'batch': planting.batch_id,
            'batch_code': planting.batch.code,
            'transplanted': transplanting.transplanted,
            'plant': planting.seeds_used.seeds.plant_variety.plant.name,
            'variety': planting.seeds_used.seeds.plant_variety.name,
            'planted': planting.planted,
            'quantity': transplanting.quantity,
            'location': transplanting.location.as_json(),
            'notes': planting.notes,
            'germination_date_early': _add_nullable_days(planting.planted, germination_min),
            'germination_date_late': _add_nullable_days(planting.planted, germination_max),
            'maturity_date_early': _add_nullable_days(
                _maturity_anchor(variety, planting.planted, transplanting.transplanted),
                maturity_min,
            ),
            'maturity_date_late': _add_nullable_days(
                _maturity_anchor(variety, planting.planted, transplanting.transplanted),
                maturity_max,
            )
        })
    specific_plant_locations = SpecificPlantLocation.objects.filter(
        specific_plant__workspace=workspace,
        location_type=SpecificPlantLocation.GARDEN_SQUARE,
        ended__isnull=True,
    ).select_related(
        'specific_plant__cell_planting__seed_tray_planting__seeds_used__seeds__plant_variety__plant',
        'specific_plant__cell_planting__seed_tray_planting__batch',
        'specific_plant__batch__variety__plant',
        'specific_plant__garden_planting',
        'garden_square__bed__area',
    )
    for location in specific_plant_locations:
        plant = location.specific_plant
        quick_origin = plant.garden_planting
        planting = plant.cell_planting.seed_tray_planting if plant.cell_planting_id else None
        variety = plant.batch.variety
        germination_min, germination_max, maturity_min, maturity_max = _get_variety_days(variety)
        planted = quick_origin.recorded_on if quick_origin else planting.planted
        planting_data.append({
            'specific_plant_pk': plant.pk,
            'transplanting_pk': location.pk,
            'garden_planting_pk': quick_origin.pk if quick_origin else None,
            'planting_pk': quick_origin.pk if quick_origin else planting.pk,
            'batch': plant.batch_id,
            'batch_code': plant.batch.code,
            'transplanted': location.started,
            'plant': variety.plant.name,
            'variety': variety.name,
            'planted': planted,
            'quantity': 1,
            'source': quick_origin.source if quick_origin else None,
            'perennial': quick_origin.perennial if quick_origin else False,
            'date_is_approximate': quick_origin.date_is_approximate if quick_origin else False,
            'location': location.garden_square.as_json(),
            'notes': location.notes or plant.notes,
            'germination_date_early': _add_nullable_days(planted, germination_min),
            'germination_date_late': _add_nullable_days(planted, germination_max),
            'maturity_date_early': _add_nullable_days(
                _maturity_anchor(variety, planted, location.started), maturity_min,
            ),
            'maturity_date_late': _add_nullable_days(
                _maturity_anchor(variety, planted, location.started), maturity_max,
            ),
        })
    return JsonResponse({'plantings': planting_data})


@login_required
def gardensquare_complete(request):
    """
    Harvest Complete/Remove the remaining contents of a garden square
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    data = get_request_data(request)

    planting = get_object_or_404(
        GardenSquareDirectSowPlanting,
        pk=data.get('planting'),
        workspace=get_current_workspace(),
    )
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)


@login_required
def gardensquare_transplant_complete(request):
    """
    Harvest Complete/Remove the remaining contents of a garden square (that was transplanted)
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    data = get_request_data(request)

    planting = get_object_or_404(
        GardenSquareTransplant,
        pk=data.get('planting'),
        workspace=get_current_workspace(),
    )
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)
