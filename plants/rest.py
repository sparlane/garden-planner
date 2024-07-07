from rest_framework import routers, serializers, viewsets

from .models import PlantFamily, Plant, PlantVariety


class PlantFamilySerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantFamily
        fields = ['pk', 'name', 'notes']


class PlantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plant
        fields = ['pk', 'family', 'name', 'notes', 'spacing', 'inter_row_spacing', 'plants_per_square_foot', 'germination_days_min', 'germination_days_max', 'maturity_days_min', 'maturity_days_max']


class PlantVarietySerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantVariety
        fields = ['pk', 'plant', 'name', 'notes', 'spacing', 'inter_row_spacing', 'plants_per_square_foot', 'germination_days_min', 'germination_days_max', 'maturity_days_min', 'maturity_days_max']


class PlantFamilyViewSet(viewsets.ModelViewSet):
    queryset = PlantFamily.objects.all()
    serializer_class = PlantFamilySerializer


class PlantViewSet(viewsets.ModelViewSet):
    queryset = Plant.objects.all()
    serializer_class = PlantSerializer


class PlantVarietyViewSet(viewsets.ModelViewSet):
    queryset = PlantVariety.objects.all()
    serializer_class = PlantVarietySerializer


router = routers.DefaultRouter()
router.register(r'family', PlantFamilyViewSet)
router.register(r'plant', PlantViewSet)
router.register(r'variety', PlantVarietyViewSet)
