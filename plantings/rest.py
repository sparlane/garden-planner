from rest_framework import routers, serializers, viewsets

from .models import GardenRowDirectSowPlanting, GardenSquareDirectSowPlanting, SeedTrayPlanting


class GardenRowDirectSowPlantingSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenRowDirectSowPlanting
        fields = ['planted', 'seeds_used', 'quantity', 'location', 'notes']


class GardenSquareDirectSowSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenRowDirectSowPlanting
        fields = ['planted', 'seeds_used', 'quantity', 'location', 'notes']


class SeedTrayPlantingSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = SeedTrayPlanting
        fields = ['planted', 'seeds_used', 'quantity', 'location', 'notes']


class GardenRowDirectSowPlantingViewSet(viewsets.ModelViewSet):
    queryset = GardenRowDirectSowPlanting.objects.all()
    serializer_class = GardenRowDirectSowPlantingSerializer


class GardenSquareDirectSowPlantingViewSet(viewsets.ModelViewSet):
    queryset = GardenSquareDirectSowPlanting.objects.all()
    serializer_class = GardenSquareDirectSowSerializer


class SeedTrayPlantingViewSet(viewsets.ModelViewSet):
    queryset = SeedTrayPlanting.objects.all()
    serializer_class = SeedTrayPlantingSerializer


router = routers.DefaultRouter()
router.register(r'directsowgardenrow', GardenRowDirectSowPlantingViewSet)
router.register(r'directsowgardensquare', GardenSquareDirectSowPlantingViewSet)
router.register(r'seedtray', SeedTrayPlantingViewSet)
