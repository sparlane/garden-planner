from rest_framework import routers, serializers, viewsets

from .models import GardenArea, GardenBed


class GardenAreaSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenArea
        fields = ['name', 'size_x', 'size_y']


class GardenBedSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenBed
        fields = ['area', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenAreaViewSet(viewsets.ModelViewSet):
    queryset = GardenArea.objects.all()
    serializer_class = GardenAreaSerializer


class GardenBedViewSet(viewsets.ModelViewSet):
    queryset = GardenBed.objects.all()
    serializer_class = GardenBedSerializer


router = routers.DefaultRouter()
router.register(r'areas', GardenAreaViewSet)
router.register(r'beds', GardenBedViewSet)