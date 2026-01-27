"""
Rest related classes for seed trays
"""
from django.db import transaction
from itertools import product
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

    def create(self, validated_data):
        """
        Override create to automatically generate SeedTrayCells
        """
        with transaction.atomic():
            seed_tray = super().create(validated_data)
            model = seed_tray.model

            # Create a cell for each position in the tray
            cells = [
                SeedTrayCell(tray=seed_tray, x_position=x, y_position=y)
                for x, y in product(range(model.x_cells), range(model.y_cells))
            ]
            SeedTrayCell.objects.bulk_create(cells)
        return seed_tray


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
