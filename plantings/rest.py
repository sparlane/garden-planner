"""
Rest for Plantings
"""
from rest_framework import routers, serializers, viewsets

from .models import GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting, SeedTrayPlanting, GardenSquareTransplant


class GardenRowDirectSowPlantingSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenRowDirectSowPlanting
    """
    class Meta:
        model = GardenRowDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'quantity', 'location', 'removed', 'notes']


class GardenSquareDirectSowSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenSquareDirectSowPlanting
    """
    class Meta:
        model = GardenSquareDirectSowPlanting
        fields = ['pk', 'planted', 'seeds_used', 'quantity', 'location', 'removed', 'notes']


class SeedTrayPlantingSerializer(serializers.ModelSerializer):
    """
    Serializer for SeedTrayPlanting
    """
    class Meta:
        model = SeedTrayPlanting
        fields = ['pk', 'planted', 'seeds_used', 'quantity', 'seed_tray', 'location', 'removed', 'notes']


class GardenSquareTransplantSerializer(serializers.ModelSerializer):
    """
    Serializer for GardenSquareTransplant
    """
    class Meta:
        model = GardenSquareTransplant
        fields = ['pk', 'transplanted', 'original_planting', 'quantity', 'location', 'removed', 'notes']


class GardenRowDirectSowPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenRowDirectSowPlanting
    """
    queryset = GardenRowDirectSowPlanting.objects.all()
    serializer_class = GardenRowDirectSowPlantingSerializer


class GardenSquareDirectSowPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenSquareDirectSowPlanting
    """
    queryset = GardenSquareDirectSowPlanting.objects.all()
    serializer_class = GardenSquareDirectSowSerializer


class SeedTrayPlantingViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer


class SeedTrayPlantingViewSeedTraySet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayPlanting filtered by SeedTray
    """
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer

    def get_queryset(self):
        return self.queryset.filter(seed_tray__pk=self.kwargs['seed_tray_pk'])


class GardenSquareTransplantViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of GardenSquareTransplant
    """
    queryset = GardenSquareTransplant.objects.all()
    serializer_class = GardenSquareTransplantSerializer


router = routers.SimpleRouter()
router.register(r'directsowgardenrow', GardenRowDirectSowPlantingViewSet)
router.register(r'directsowgardensquare', GardenSquareDirectSowPlantingViewSet)
router.register(r'seedtray', SeedTrayPlantingViewSet)
router.register(r'transplantedgardensquare', GardenSquareTransplantViewSet)

router.register(r'seedtray-data/(?P<seed_tray_pk>[^/.]+)/plantings', SeedTrayPlantingViewSeedTraySet, basename='seedtray-plantings')
