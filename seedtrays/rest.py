"""
Rest related classes for seed trays
"""
from rest_framework import routers, serializers, viewsets

from .models import SeedTrayModel, SeedTray, SeedTrayCell


class SeedTrayModelSerializer(serializers.ModelSerializer):
    """
    Serializer for a SeedTrayModel
    """
    class Meta:
        model = SeedTrayModel
        fields = ['pk', 'identifier', 'description', 'height', 'x_size', 'y_size', 'x_cells', 'y_cells', 'cell_size_ml']


class SeedTraySerializer(serializers.ModelSerializer):
    """
    Serializer for a Seed Tray
    """
    class Meta:
        model = SeedTray
        fields = ['pk', 'model', 'created', 'notes']


class SeedTrayCellSerializer(serializers.ModelSerializer):
    """
    Serializer for a Seed Tray Cell
    """
    class Meta:
        model = SeedTrayCell
        fields = ['pk', 'tray', 'x_position', 'y_position']


class SeedTrayModelsViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedTrayModels
    """
    queryset = SeedTrayModel.objects.all()
    serializer_class = SeedTrayModelSerializer


class SeedTrayAllViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrays
    """
    queryset = SeedTray.objects.all()
    serializer_class = SeedTraySerializer


class SeedTrayCellViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedTrayCells
    """
    queryset = SeedTrayCell.objects.all()
    serializer_class = SeedTrayCellSerializer


router = routers.DefaultRouter()
router.register(r'seedtraymodels', SeedTrayModelsViewSet)
router.register(r'seedtrays', SeedTrayAllViewSet)
router.register(r'seedtraycells', SeedTrayCellViewSet)
