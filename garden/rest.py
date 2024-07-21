"""
Rest for Gardens
"""
from rest_framework import routers, serializers, viewsets

from .models import GardenArea, GardenBed, GardenRow, GardenSquare


class GardenAreaSerializer(serializers.ModelSerializer):
    """
    Serializer Garden Area
    """
    class Meta:
        model = GardenArea
        fields = ['pk', 'name', 'size_x', 'size_y']


class GardenBedSerializer(serializers.ModelSerializer):
    """
    Serializer for Garden Bed
    """
    class Meta:
        model = GardenBed
        fields = ['pk', 'area', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenRowSerializer(serializers.ModelSerializer):
    """
    Serializer for Garden Row
    """
    class Meta:
        model = GardenRow
        fields = ['pk', 'bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenSquareSerializer(serializers.ModelSerializer):
    """
    Serializer for Garden Square
    """
    class Meta:
        model = GardenSquare
        fields = ['pk', 'bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']


class GardenAreaViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Area
    """
    queryset = GardenArea.objects.all()
    serializer_class = GardenAreaSerializer


class GardenBedViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Bed
    """
    queryset = GardenBed.objects.all()
    serializer_class = GardenBedSerializer


class GardenRowViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Row
    """
    queryset = GardenRow.objects.all()
    serializer_class = GardenRowSerializer


class GardenSquareViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Square
    """
    queryset = GardenSquare.objects.all()
    serializer_class = GardenSquareSerializer


router = routers.DefaultRouter()
router.register(r'areas', GardenAreaViewSet)
router.register(r'beds', GardenBedViewSet)
router.register(r'rows', GardenRowViewSet)
router.register(r'squares', GardenSquareViewSet)
