from rest_framework import routers, serializers, viewsets

from .models import GardenArea, GardenBed, GardenRow, GardenSquare


class GardenAreaSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenArea
        fields = ['name', 'size_x', 'size_y']


class GardenBedSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenBed
        fields = ['area', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenRowSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenRow
        fields = ['bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenSquareSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = GardenSquare
        fields = ['bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenAreaViewSet(viewsets.ModelViewSet):
    queryset = GardenArea.objects.all()
    serializer_class = GardenAreaSerializer


class GardenBedViewSet(viewsets.ModelViewSet):
    queryset = GardenBed.objects.all()
    serializer_class = GardenBedSerializer


class GardenRowViewSet(viewsets.ModelViewSet):
    queryset = GardenRow.objects.all()
    serializer_class = GardenRowSerializer


class GardenSquareViewSet(viewsets.ModelViewSet):
    queryset = GardenSquare.objects.all()
    serializer_class = GardenSquareSerializer


router = routers.DefaultRouter()
router.register(r'areas', GardenAreaViewSet)
router.register(r'beds', GardenBedViewSet)
router.register(r'rows', GardenRowViewSet)
router.register(r'squares', GardenSquareViewSet)