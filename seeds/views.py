"""
Views for seeds
"""


from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404

from .models import SeedPacket


def packets_current(request):
    """
    List the seed packets that are not empty
    """
    packets = SeedPacket.objects.select_related('seeds', 'seeds__plant_variety', 'seeds__plant_variety__plant', 'seeds__supplier').filter(empty=False).order_by('seeds__plant_variety__plant__name', 'seeds__plant_variety__name')
    packet_data = [
        {
            'pk': packet.pk,
            'plant': packet.seeds.plant_variety.plant.name,
            'variety': packet.seeds.plant_variety.name,
            'supplier': packet.seeds.supplier.name,
            'purchase_date': packet.purchase_date.isoformat() if packet.purchase_date else None,
            'sow_by': packet.sow_by.isoformat() if packet.sow_by else None,
            'notes': packet.notes
        }
        for packet in packets
    ]
    return JsonResponse({'packets': packet_data})


def packets_empty(request):
    """
    Complete/Remove the remaining contents of a seed tray
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    packet = get_object_or_404(SeedPacket, pk=request.POST.get('packet'))
    packet.empty = True
    packet.save()
    return HttpResponse(status=204)
