"""
Rest access for plants
"""
from rest_framework import routers, serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from common.catalog import CatalogViewSetMixin
from common.retirement import RetirementSerializerMixin
from workspaces.current import get_current_workspace
from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .models import PlantFamily, Plant, PlantVariety
from .reference_sets import ReferenceSetSerializer, export_reference_set, install_reference_set
from .starters import ensure_starter_crops


class ReferenceSerializerMixin(serializers.Serializer):  # pylint: disable=abstract-method
    """Report which of a record's facts are still the reference set's.

    Both halves are read-only. ``reference_source`` says where the record came
    from and is not something a gardener sets, and ``reference_fields`` is read
    off the record by comparison rather than stored, so editing a figure is
    what stops it being the set's -- there is no second write to make.
    """

    reference_fields = serializers.SerializerMethodField()

    def get_reference_fields(self, instance):
        """Return the fields still saying exactly what the set supplied."""
        return instance.reference_fields()


class PlantFamilySerializer(
    ReferenceSerializerMixin,
    RetirementSerializerMixin,
    serializers.ModelSerializer,
):
    """
    Serializer for Plant Family
    """
    class Meta:
        model = PlantFamily
        fields = [
            'pk', 'name', 'notes', 'active', 'merged_into',
            'reference_source', 'reference_fields',
        ]


class PlantSerializer(
    ReferenceSerializerMixin,
    RetirementSerializerMixin,
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
    """
    Serializer for Plant
    """
    class Meta:
        model = Plant
        fields = [
            'pk', 'family', 'name', 'notes', 'spacing', 'inter_row_spacing',
            'plants_per_square_foot', 'germination_days_min',
            'germination_days_max', 'maturity_days_min', 'maturity_days_max',
            'maturity_basis', 'active', 'merged_into',
            'reference_source', 'reference_fields',
        ]

    workspace_field_lookups = {'family': 'workspace'}


class PlantVarietySerializer(
    ReferenceSerializerMixin,
    RetirementSerializerMixin,
    CurrentWorkspaceSerializerMixin,
    serializers.ModelSerializer,
):
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
            'merged_into', 'reference_source', 'reference_fields',
        ]
        read_only_fields = ['effective_maturity_basis']

    workspace_field_lookups = {'plant': 'workspace'}


class PlantFamilyViewSet(CatalogViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plant Family
    """
    queryset = PlantFamily.objects.all()
    serializer_class = PlantFamilySerializer
    pagination_class = None

    #: A family is found by anything filed under it, which is two levels of
    #: names rather than one query per family.
    search_related = ('plant_set__plantvariety_set',)


class PlantViewSet(CatalogViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plants
    """
    queryset = Plant.objects.all()
    serializer_class = PlantSerializer
    pagination_class = None

    search_related = ('family', 'plantvariety_set')


class PlantVarietyViewSet(CatalogViewSetMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet of Plant Varieties
    """
    queryset = PlantVariety.objects.order_by('pk')
    serializer_class = PlantVarietySerializer

    search_related = ('plant__family',)


def installed_response(request, installed):
    """Report the catalog a set covers, whichever set was just installed.

    What the starter button and an imported document leave behind is the same
    answer to the same question -- what does the catalog hold now -- so both
    report it the same way. Records that were only adopted or left alone are
    included, because a screen showing only what changed would look as though
    a set had done nothing the second time it was asked.
    """
    families, plants, varieties = installed
    context = {'request': request}
    return Response(
        {
            'families': PlantFamilySerializer(families, many=True, context=context).data,
            'plants': PlantSerializer(plants, many=True, context=context).data,
            'varieties': PlantVarietySerializer(varieties, many=True, context=context).data,
        },
        status=status.HTTP_201_CREATED,
    )


class StarterCropsView(APIView):
    """Install the crops a household garden usually holds."""

    def post(self, request):
        """Install any starter crop that is missing and report the whole set.

        Asking twice creates nothing the second time and argues with nothing
        the gardener has done in between, so this is safe to offer from a
        catalog screen rather than only once at setup.
        """
        return installed_response(request, ensure_starter_crops(get_current_workspace()))


class ReferenceSetView(APIView):
    """Carry a crop catalog out of this garden, or bring one in.

    One route in both directions, because it is one document: what a GET
    writes out is what a POST accepts, so a catalog exported from one garden
    installs into the next without anything in between having to agree about a
    format twice.
    """

    def get(self, request):  # pylint: disable=unused-argument
        """Return this workspace's active catalog as a document."""
        return Response(export_reference_set(get_current_workspace()))

    def post(self, request):
        """Install a document and report what the catalog now holds.

        Validated before anything is written, so a document with one bad crop
        in it installs none of it: an operator who has to look at a file to
        find out how much of it arrived is worse off than one told it was
        refused.
        """
        document = ReferenceSetSerializer(data=request.data)
        document.is_valid(raise_exception=True)
        return installed_response(
            request, install_reference_set(get_current_workspace(), document.validated_data),
        )


router = routers.DefaultRouter()
router.register(r'family', PlantFamilyViewSet)
router.register(r'plant', PlantViewSet)
router.register(r'variety', PlantVarietyViewSet)
