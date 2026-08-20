"""
Rest related classes for supplies
"""
from rest_framework import routers, serializers, viewsets

from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .models import Supplier


class SupplierSerializer(serializers.ModelSerializer):
    """
    Serializer for a Supplier
    """
    class Meta:
        model = Supplier
        fields = ['pk', 'name', 'website', 'notes', 'is_system_default']
        read_only_fields = ['is_system_default']


class SupplierViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Suppliers
    """
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


router = routers.DefaultRouter()
router.register(r'supplier', SupplierViewSet)
