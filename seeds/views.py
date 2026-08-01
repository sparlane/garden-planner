"""
Views for seeds
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404

from gp.utils import get_request_data
from plantings.models import (
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)

from workspaces.models import get_current_workspace

from .models import SeedPacket


def coalesced_sum(model, lookup):
    """Return a Coalesce(Subquery(SUM(quantity)), 0) annotation for a reverse-FK usage count.

    Subquery is used instead of a direct Sum() to prevent fan-out double-counting:
    annotating with multiple Sum()s that traverse overlapping JOIN paths (e.g. both
    seeds_planted_trays and transplanted_count go through seedtrayplanting) causes
    Django to multiply rows, producing incorrect totals.
    """
    subq = (
        model.objects
        .filter(**{lookup: OuterRef('pk')})
        .values(lookup)
        .annotate(t=Sum('quantity'))
        .values('t')
    )
    return Coalesce(Subquery(subq, output_field=IntegerField()), Value(0))


def get_transplanted_counts(packet_ids):
    """Count distinct plants per packet, with legacy fallback per sowing."""
    individual_counts = {
        planting_id: (packet_id, count)
        for planting_id, packet_id, count in (
            SpecificPlant.objects
            .filter(
                cell_planting__seed_tray_planting__seeds_used_id__in=packet_ids,
                locations__location_type=SpecificPlantLocation.GARDEN_SQUARE,
            )
            .values_list(
                'cell_planting__seed_tray_planting_id',
                'cell_planting__seed_tray_planting__seeds_used_id',
            )
            .annotate(total=Count('pk', distinct=True))
        )
    }
    legacy_counts = {
        planting_id: (packet_id, count)
        for planting_id, packet_id, count in (
            GardenSquareTransplant.objects
            .filter(original_planting__seeds_used_id__in=packet_ids)
            .values_list(
                'original_planting_id',
                'original_planting__seeds_used_id',
            )
            .annotate(total=Sum('quantity'))
        )
    }

    transplanted_counts = dict.fromkeys(packet_ids, 0)
    for planting_id in individual_counts.keys() | legacy_counts.keys():
        if planting_id in individual_counts:
            packet_id, count = individual_counts[planting_id]
        else:
            packet_id, count = legacy_counts[planting_id]
        transplanted_counts[packet_id] += count
    return transplanted_counts


@login_required
def packets_current(request):
    """
    List the seed packets that are not empty
    """
    packets = list(
        SeedPacket.objects
        .select_related('seeds', 'seeds__plant_variety', 'seeds__plant_variety__plant', 'seeds__supplier')
        .filter(empty=False, workspace=get_current_workspace())
        .annotate(
            seeds_planted_trays=coalesced_sum(SeedTrayPlanting, 'seeds_used'),
            seeds_planted_direct=coalesced_sum(GardenSquareDirectSowPlanting, 'seeds_used'),
        )
        .order_by('seeds__plant_variety__plant__name', 'seeds__plant_variety__name')
    )
    transplanted_counts = get_transplanted_counts([packet.pk for packet in packets])
    packet_data = [
        {
            'pk': packet.pk,
            'plant': packet.seeds.plant_variety.plant.name,
            'variety': packet.seeds.plant_variety.name,
            'supplier': packet.seeds.supplier.name,
            'purchase_date': packet.purchase_date.isoformat() if packet.purchase_date else None,
            'sow_by': packet.sow_by.isoformat() if packet.sow_by else None,
            'notes': packet.notes,
            'seeds_planted_trays': packet.seeds_planted_trays,
            'seeds_planted_direct': packet.seeds_planted_direct,
            'transplanted_count': transplanted_counts[packet.pk],
        }
        for packet in packets
    ]
    return JsonResponse({'packets': packet_data})


@login_required
def packets_empty(request):
    """
    Complete/Remove the remaining contents of a seed tray
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    data = get_request_data(request)

    packet = get_object_or_404(
        SeedPacket,
        pk=data.get('packet'),
        workspace=get_current_workspace(),
    )
    packet.empty = True
    packet.save()
    return HttpResponse(status=204)
