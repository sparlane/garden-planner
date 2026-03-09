"""
Views for seeds
"""

from django.contrib.auth.decorators import login_required
from django.db.models import IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404

from gp.utils import get_request_data
from plantings.models import SeedTrayPlanting, GardenSquareDirectSowPlanting, GardenSquareTransplant

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


@login_required
def packets_current(request):
    """
    List the seed packets that are not empty
    """
    packets = (
        SeedPacket.objects
        .select_related('seeds', 'seeds__plant_variety', 'seeds__plant_variety__plant', 'seeds__supplier')
        .filter(empty=False)
        .annotate(
            seeds_planted_trays=coalesced_sum(SeedTrayPlanting, 'seeds_used'),
            seeds_planted_direct=coalesced_sum(GardenSquareDirectSowPlanting, 'seeds_used'),
            transplanted_count=coalesced_sum(GardenSquareTransplant, 'original_planting__seeds_used'),
        )
        .order_by('seeds__plant_variety__plant__name', 'seeds__plant_variety__name')
    )
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
            'transplanted_count': packet.transplanted_count,
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

    packet = get_object_or_404(SeedPacket, pk=data.get('packet'))
    packet.empty = True
    packet.save()
    return HttpResponse(status=204)
