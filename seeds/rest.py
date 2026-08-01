"""
Rest related classes for seeds
"""
from rest_framework import routers, serializers, viewsets

from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import Seeds, SeedPacket


class SeedsSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for a Seeds Supply
    """
    class Meta:
        model = Seeds
        fields = ['pk', 'supplier', 'plant_variety', 'supplier_code', 'url', 'notes']

    workspace_field_lookups = {
        'supplier': 'workspace',
        'plant_variety': 'workspace',
    }


class SeedPacketSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for a Seed Packet
    """
    class Meta:
        model = SeedPacket
        fields = ['pk', 'seeds', 'purchase_date', 'sow_by', 'empty', 'notes']

    workspace_field_lookups = {'seeds': 'workspace'}


class SeedsViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Seeds
    """
    queryset = Seeds.objects.all()
    serializer_class = SeedsSerializer


class SeedPacketCurrentViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of non-empty SeedPackets
    """
    queryset = SeedPacket.objects.filter(empty=False)
    serializer_class = SeedPacketSerializer


class SeedPacketAllViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of all SeedPackets
    """
    queryset = SeedPacket.objects.all()
    serializer_class = SeedPacketSerializer


router = routers.DefaultRouter()
router.register(r'seeds', SeedsViewSet)
router.register(r'packets/all', SeedPacketAllViewSet, 'AllSeedPackets')
router.register(r'packets', SeedPacketCurrentViewSet)
