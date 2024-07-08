"""
Rest related classes for seeds
"""
from rest_framework import routers, serializers, viewsets

from .models import Supplier, Seeds, SeedPacket


class SupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for a Supplier
    """
    class Meta:
        model = Supplier
        fields = ['pk', 'name', 'website', 'notes']


class SeedsSerializer(serializers.ModelSerializer):
    """
    Serializer for a Seeds Supply
    """
    class Meta:
        model = Seeds
        fields = ['pk', 'supplier', 'plant_variety', 'supplier_code', 'url', 'notes']


class SeedPacketSerializer(serializers.ModelSerializer):
    """
    Serializer for a Seed Packet
    """
    class Meta:
        model = SeedPacket
        fields = ['pk', 'seeds', 'purchase_date', 'sow_by', 'notes']


class SupplierViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Suppliers
    """
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class SeedsViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Seeds
    """
    queryset = Seeds.objects.all()
    serializer_class = SeedsSerializer


class SeedPacketViewSet(viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of SeedPackets
    """
    queryset = SeedPacket.objects.all()
    serializer_class = SeedPacketSerializer


router = routers.DefaultRouter()
router.register(r'supplier', SupplierViewSet)
router.register(r'seeds', SeedsViewSet)
router.register(r'packets', SeedPacketViewSet)
