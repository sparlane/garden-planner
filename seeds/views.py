"""
Views for seeds
"""


from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404

from .models import SeedPacket


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
