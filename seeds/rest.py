from rest_framework import routers, serializers, viewsets

from .models import Supplier, Seeds, SeedPacket


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['pk', 'name', 'website', 'notes']


class SeedsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Seeds
        fields = ['pk', 'supplier', 'plant_variety', 'supplier_code', 'url', 'notes']


class SeedPacketSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeedPacket
        fields = ['pk', 'seeds', 'purchase_date', 'sow_by', 'notes']


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class SeedsViewSet(viewsets.ModelViewSet):
    queryset = Seeds.objects.all()
    serializer_class = SeedsSerializer


class SeedPacketViewSet(viewsets.ModelViewSet):
    queryset = SeedPacket.objects.all()
    serializer_class = SeedPacketSerializer


router = routers.DefaultRouter()
router.register(r'supplier', SupplierViewSet)
router.register(r'seeds', SeedsViewSet)
router.register(r'packets', SeedPacketViewSet)