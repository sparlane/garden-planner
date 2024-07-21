"""
Planting views
"""

import datetime

from django.http import JsonResponse

from .models import SeedTrayPlanting, GardenSquareDirectSowPlanting


def seedtray_current(request):
    """
    List the seedtray plantings that are currently growing
    """
    plantings = SeedTrayPlanting.objects.all()
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


def gardensquare_current(request):
    """
    List the GardenSquare plantings that are currently growing
    """
    plantings = GardenSquareDirectSowPlanting.objects.all()
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
            'pk': planting.pk,
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
    return JsonResponse({'plantings': planting_data})
