"""
Planting views
"""

import datetime

from django.http import HttpResponseNotAllowed, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404

from .models import SeedTrayPlanting, GardenSquareDirectSowPlanting, GardenSquareTransplant


def seedtray_current(request):
    """
    List the seedtray plantings that are currently growing
    """
    plantings = SeedTrayPlanting.objects.filter(removed=False).order_by('planted')
    planting_data = []
    for planting in plantings:
        variety = planting.seeds_used.seeds.plant_variety
        germination_min = variety.plant.germination_days_min
        germination_max = variety.plant.germination_days_max
        if variety.germination_days_min:
            germination_min = variety.germination_days_min
        if variety.germination_days_max:
            germination_max = variety.germination_days_max
        planting_data.append({
            'pk': planting.pk,
            'plant': planting.seeds_used.seeds.plant_variety.plant.name,
            'variety': planting.seeds_used.seeds.plant_variety.name,
            'planted': planting.planted,
            'quantity': planting.quantity,
            'location': planting.location,
            'notes': planting.notes,
            'germination_date_early': planting.planted + datetime.timedelta(days=germination_min),
            'germination_date_late': planting.planted + datetime.timedelta(days=germination_max)
        })
    return JsonResponse({'plantings': planting_data})


def seedtray_complete(request):
    """
    Complete/Remove the remaining contents of a seed tray
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    planting = get_object_or_404(SeedTrayPlanting, pk=request.POST.get('planting'))
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)


def gardensquare_current(request):
    """
    List the GardenSquare plantings that are currently growing
    """
    plantings = GardenSquareDirectSowPlanting.objects.filter(removed=False).order_by('planted')
    planting_data = []
    for planting in plantings:
        variety = planting.seeds_used.seeds.plant_variety
        germination_min = variety.plant.germination_days_min
        germination_max = variety.plant.germination_days_max
        maturity_min = variety.plant.maturity_days_min
        maturity_max = variety.plant.maturity_days_max
        if variety.germination_days_min:
            germination_min = variety.germination_days_min
        if variety.germination_days_max:
            germination_max = variety.germination_days_max
        if variety.maturity_days_min:
            maturity_min = variety.maturity_days_min
        if variety.maturity_days_max:
            maturity_max = variety.maturity_days_max
        planting_data.append({
            'planting_pk': planting.pk,
            'plant': planting.seeds_used.seeds.plant_variety.plant.name,
            'variety': planting.seeds_used.seeds.plant_variety.name,
            'planted': planting.planted,
            'quantity': planting.quantity,
            'location': planting.location.as_json(),
            'notes': planting.notes,
            'germination_date_early': planting.planted + datetime.timedelta(days=germination_min),
            'germination_date_late': planting.planted + datetime.timedelta(days=germination_max),
            'maturity_date_early': planting.planted + datetime.timedelta(days=maturity_min),
            'maturity_date_late': planting.planted + datetime.timedelta(days=maturity_max)
        })
    transplantings = GardenSquareTransplant.objects.filter(removed=False)
    for transplanting in transplantings:
        planting = transplanting.original_planting
        variety = planting.seeds_used.seeds.plant_variety
        germination_min = variety.plant.germination_days_min
        germination_max = variety.plant.germination_days_max
        maturity_min = variety.plant.maturity_days_min
        maturity_max = variety.plant.maturity_days_max
        if variety.germination_days_min:
            germination_min = variety.germination_days_min
        if variety.germination_days_max:
            germination_max = variety.germination_days_max
        if variety.maturity_days_min:
            maturity_min = variety.maturity_days_min
        if variety.maturity_days_max:
            maturity_max = variety.maturity_days_max
        planting_data.append({
            'transplanting_pk': transplanting.pk,
            'planting_pk': planting.pk,
            'transplanted': transplanting.transplanted,
            'plant': planting.seeds_used.seeds.plant_variety.plant.name,
            'variety': planting.seeds_used.seeds.plant_variety.name,
            'planted': planting.planted,
            'quantity': transplanting.quantity,
            'location': transplanting.location.as_json(),
            'notes': planting.notes,
            'germination_date_early': planting.planted + datetime.timedelta(days=germination_min),
            'germination_date_late': planting.planted + datetime.timedelta(days=germination_max),
            'maturity_date_early': transplanting.transplanted + datetime.timedelta(days=maturity_min),
            'maturity_date_late': transplanting.transplanted + datetime.timedelta(days=maturity_max)
        })
    return JsonResponse({'plantings': planting_data})


def gardensquare_complete(request):
    """
    Harvest Complete/Remove the remaining contents of a garden square
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    planting = get_object_or_404(GardenSquareDirectSowPlanting, pk=request.POST.get('planting'))
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)


def gardensquare_transplant_complete(request):
    """
    Harvest Complete/Remove the remaining contents of a garden square (that was transplanted)
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    planting = get_object_or_404(GardenSquareTransplant, pk=request.POST.get('planting'))
    planting.removed = True
    planting.save()
    return HttpResponse(status=204)
