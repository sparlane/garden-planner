"""
Rest for Gardens
"""
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from workspaces.scoping import CurrentWorkspaceSerializerMixin, CurrentWorkspaceViewSetMixin

from .geometry import latest_confirmation, measure
from .models import (
    GardenArea,
    GardenBed,
    GardenGeometryConfirmation,
    GardenRow,
    GardenSquare,
)


#: Distinguishes "not derived yet" from a legitimately unconfirmed area, so a
#: cached None is not recomputed for every field of the same response.
_UNRESOLVED = object()


def _model_errors(error):
    """Translate a Django validation error into DRF's field-error shape."""
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class GardenGeometryConfirmationSerializer(serializers.ModelSerializer):
    """
    Serializer for one recorded statement of an area's physical scale
    """
    class Meta:
        model = GardenGeometryConfirmation
        fields = ['pk', 'area', 'length_unit', 'cell_length', 'notes', 'confirmed_at']
        read_only_fields = ['pk', 'area', 'confirmed_at']


class ConfirmGeometrySerializer(serializers.Serializer):
    """
    Operator statement of what one grid step measures
    """
    length_unit = serializers.ChoiceField(
        choices=GardenGeometryConfirmation.LengthUnit.choices,
    )
    cell_length = serializers.DecimalField(max_digits=12, decimal_places=6)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class GardenAreaSerializer(serializers.ModelSerializer):
    """
    Serializer Garden Area
    """
    geometry_confirmed = serializers.SerializerMethodField()
    length_unit = serializers.SerializerMethodField()
    cell_length = serializers.SerializerMethodField()
    square_metres = serializers.SerializerMethodField()

    class Meta:
        model = GardenArea
        fields = [
            'pk',
            'name',
            'size_x',
            'size_y',
            'geometry_confirmed',
            'length_unit',
            'cell_length',
            'square_metres',
        ]

    def _confirmation(self, area):
        """Return the governing confirmation once per serialized area."""
        cached = getattr(area, '_geometry_confirmation', _UNRESOLVED)
        if cached is _UNRESOLVED:
            cached = latest_confirmation(area.geometry_confirmations.all())
            setattr(area, '_geometry_confirmation', cached)
        return cached

    def get_geometry_confirmed(self, area):
        """Report whether this area's integers have a stated physical meaning."""
        return self._confirmation(area) is not None

    def get_length_unit(self, area):
        """Report the confirmed unit, or null while the area is unconfirmed."""
        confirmation = self._confirmation(area)
        return confirmation.length_unit if confirmation else None

    def get_cell_length(self, area):
        """Report the confirmed length of one grid step as a fixed string."""
        confirmation = self._confirmation(area)
        return f'{confirmation.cell_length:.6f}' if confirmation else None

    def get_square_metres(self, area):
        """Report the area's normalized extent, or null while unconfirmed."""
        confirmation = self._confirmation(area)
        if confirmation is None:
            return None
        return f'{measure(confirmation, area):.6f}'


class GardenBedSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Garden Bed
    """
    class Meta:
        model = GardenBed
        fields = ['pk', 'area', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']

    workspace_field_lookups = {'area': 'workspace'}


class GardenRowSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Garden Row
    """
    class Meta:
        model = GardenRow
        fields = ['pk', 'bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']

    workspace_field_lookups = {'bed': 'workspace'}


class GardenSquareSerializer(CurrentWorkspaceSerializerMixin, serializers.ModelSerializer):
    """
    Serializer for Garden Square
    """
    class Meta:
        model = GardenSquare
        fields = ['pk', 'bed', 'name', 'placement_x', 'placement_y', 'size_x', 'size_y']

    workspace_field_lookups = {'bed': 'workspace'}


class GardenAreaViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Area
    """
    queryset = GardenArea.objects.prefetch_related('geometry_confirmations')
    serializer_class = GardenAreaSerializer

    @action(detail=True, methods=['post'], url_path='confirm-geometry')
    def confirm_geometry(self, request, pk=None):  # pylint: disable=unused-argument
        """Record what one grid step of this area physically measures.

        Confirming again supersedes the previous statement without erasing it,
        which is how a mistaken unit is corrected.
        """
        area = self.get_object()
        values = ConfirmGeometrySerializer(data=request.data)
        values.is_valid(raise_exception=True)
        try:
            confirmation = GardenGeometryConfirmation.objects.create(
                workspace=area.workspace,
                area=area,
                confirmed_by=request.user if request.user.is_authenticated else None,
                **values.validated_data,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc
        return Response(
            GardenGeometryConfirmationSerializer(confirmation).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'], url_path='geometry-confirmations')
    def geometry_confirmations(self, request, pk=None):  # pylint: disable=unused-argument
        """List every statement made about this area, newest first."""
        area = self.get_object()
        return Response(
            GardenGeometryConfirmationSerializer(
                area.geometry_confirmations.order_by('-confirmed_at', '-pk'),
                many=True,
            ).data,
        )


class GardenBedViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Bed
    """
    queryset = GardenBed.objects.all()
    serializer_class = GardenBedSerializer


class GardenRowViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Row
    """
    queryset = GardenRow.objects.all()
    serializer_class = GardenRowSerializer


class GardenSquareViewSet(CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
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
