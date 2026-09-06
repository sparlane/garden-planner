"""
Rest access for plants
"""
from rest_framework import routers, serializers, viewsets

from common.retirement import RetirableViewSetMixin, RetirementSerializerMixin
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import PlantFamily, Plant, PlantVariety


class PlantFamilySerializer(RetirementSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Plant Family
    """
    class Meta:
        model = PlantFamily
        fields = ['pk', 'name', 'notes', 'active']


class PlantSerializer(RetirementSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Plant
    """
    class Meta:
        model = Plant
        fields = ['pk', 'family', 'name', 'notes', 'spacing', 'inter_row_spacing', 'plants_per_square_foot', 'germination_days_min', 'germination_days_max', 'maturity_days_min', 'maturity_days_max', 'maturity_basis', 'active']

    workspace_field_lookups = {'family': 'workspace'}


class PlantVarietySerializer(RetirementSerializerMixin, CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Plant Variety
    """
    class Meta:
        model = PlantVariety
        fields = [
            'pk', 'plant', 'name', 'notes', 'spacing', 'inter_row_spacing',
            'plants_per_square_foot', 'germination_days_min',
            'germination_days_max', 'maturity_days_min', 'maturity_days_max',
            'maturity_basis', 'effective_maturity_basis', 'active',
        ]
        read_only_fields = ['effective_maturity_basis']

    workspace_field_lookups = {'plant': 'workspace'}


class PlantFamilyViewSet(RetirableViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plant Family
    """
    queryset = PlantFamily.objects.all()
    serializer_class = PlantFamilySerializer
    pagination_class = None


class PlantViewSet(RetirableViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plants
    """
    queryset = Plant.objects.all()
    serializer_class = PlantSerializer
    pagination_class = None


class PlantVarietyViewSet(RetirableViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plant Varieties
    """
    queryset = PlantVariety.objects.order_by('pk')
    serializer_class = PlantVarietySerializer


router = routers.DefaultRouter()
router.register(r'family', PlantFamilyViewSet)
router.register(r'plant', PlantViewSet)
router.register(r'variety', PlantVarietyViewSet)
