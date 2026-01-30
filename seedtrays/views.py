from django.views import View
from django.shortcuts import get_object_or_404, render

from .models import SeedTray


class SeedTrayDetailView(View):
    def get(self, request, pk):
        seed_tray = get_object_or_404(SeedTray, pk=pk)
        context = {
            'seed_tray': seed_tray
        }
        return render(request, 'seedtrays/seedtray_detail.html', context)
