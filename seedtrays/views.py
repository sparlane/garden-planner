"""HTML views for seed trays."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.shortcuts import get_object_or_404, render

from .models import SeedTray


class SeedTrayDetailView(LoginRequiredMixin, View):
    """Render details for one seed tray."""

    def get(self, request, pk):
        """Render the requested seed tray."""
        seed_tray = get_object_or_404(SeedTray, pk=pk)
        context = {
            'seed_tray': seed_tray
        }
        return render(request, 'seedtrays/seedtray_detail.html', context)
