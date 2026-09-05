"""
Rest for Gardens
"""
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import ProtectedError

from rest_framework import routers, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.settings import api_settings

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
    if not hasattr(error, 'message_dict'):
        return error.messages
    errors = dict(error.message_dict)
    # Django files errors with no field of their own under ``__all__``; DRF
    # clients, and the frontend's error formatter, look for its own key.
    if NON_FIELD_ERRORS in errors:
        errors[api_settings.NON_FIELD_ERRORS_KEY] = errors.pop(NON_FIELD_ERRORS)
    return errors


#: The most geometry one request may lay down at once. A square-foot template
#: covering a large bed is a few hundred squares; anything past this is a typo
#: in a dimension rather than a garden.
MAX_GEOMETRY_BATCH = 400


class GeometryWriteMixin:
    """Report model-level placement failures as field errors, not as 500s.

    Beds, rows, and squares validate their placement in ``clean()`` and call
    ``full_clean()`` from ``save()``, so a bad layout raises after the
    serializer has already accepted the payload. Every write path has to
    translate that, and a delete has to explain what is still standing there
    rather than surfacing the FK protection as a server error.
    """

    def get_serializer(self, *args, **kwargs):
        """Accept a whole layout in one request as well as a single record.

        A layout template is chosen as one thing and should arrive as one
        thing: the bed and the rows or squares dividing it are meaningless
        apart, and writing them one request at a time would leave a half-built
        bed behind whenever the browser or the network gave up in the middle.
        """
        data = kwargs.get('data')
        if isinstance(data, list):
            if len(data) > MAX_GEOMETRY_BATCH:
                raise ValidationError(
                    f'{len(data)} pieces of geometry were sent at once; '
                    f'{MAX_GEOMETRY_BATCH} is the most one layout may lay down. '
                    'Check the dimensions and the spacing.',
                )
            kwargs['many'] = True
        return super().get_serializer(*args, **kwargs)

    def perform_create(self, serializer):
        """Create the records, reporting a placement failure as a field error.

        The whole batch is one transaction, so a template that collides on its
        last square leaves nothing behind. Each row is visible to the next
        one's placement check inside that transaction, which is what catches a
        template that overlaps itself.
        """
        try:
            with transaction.atomic():
                super().perform_create(serializer)
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def perform_update(self, serializer):
        """Save the change, reporting a placement failure as a field error."""
        try:
            super().perform_update(serializer)
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

    def perform_destroy(self, instance):
        """Remove the record, or explain what still refers to it."""
        try:
            super().perform_destroy(instance)
        except ProtectedError as exc:
            raise ValidationError(
                'This is still referenced by recorded activity and cannot be removed.',
            ) from exc

    @action(detail=True, methods=['post'])
    def preview(self, request, pk=None):  # pylint: disable=unused-argument
        """Validate an edit without writing it and flag recorded activity.

        The browser uses this before enabling Save.  Validation runs on a
        detached model instance so model-level containment and overlap rules
        are identical to a real PATCH, while the database remains unchanged.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        candidate = self.queryset.model()
        for field in instance._meta.concrete_fields:  # pylint: disable=protected-access
            setattr(candidate, field.attname, getattr(instance, field.attname))
        for field, value in serializer.validated_data.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean(
                validate_unique=False,
                validate_constraints=False,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_model_errors(exc)) from exc

        geometry_fields = {'placement_x', 'placement_y', 'size_x', 'size_y'}
        geometry_changed = bool(geometry_fields.intersection(serializer.validated_data))
        warnings = []
        if geometry_changed and _has_recorded_activity(instance):
            warnings.append(
                'This geometry contains a current or historical location. '
                'Its records will be preserved, but the saved map position will change.',
            )
        return Response({'valid': True, 'warnings': warnings})


def _has_recorded_activity(instance):
    """Return whether geometry at or below ``instance`` has audit records."""
    # Imported lazily to keep the garden models independent of the planting
    # app whose records point back to them.
    from applications.models import InputApplicationTarget  # pylint: disable=import-outside-toplevel
    from plantings.models import (  # pylint: disable=import-outside-toplevel
        GardenPlanting,
        GardenRowDirectSowPlanting,
        GardenSquareDirectSowPlanting,
        Harvest,
        SpecificPlantLocation,
    )

    if isinstance(instance, GardenArea):
        bed_ids = GardenBed.objects.filter(area=instance).values('pk')
        square_ids = GardenSquare.objects.filter(bed_id__in=bed_ids).values('pk')
        row_ids = GardenRow.objects.filter(bed_id__in=bed_ids).values('pk')
    elif isinstance(instance, GardenBed):
        square_ids = GardenSquare.objects.filter(bed=instance).values('pk')
        row_ids = GardenRow.objects.filter(bed=instance).values('pk')
    elif isinstance(instance, GardenSquare):
        square_ids = GardenSquare.objects.filter(pk=instance.pk).values('pk')
        row_ids = GardenRow.objects.none().values('pk')
    else:
        square_ids = GardenSquare.objects.none().values('pk')
        row_ids = GardenRow.objects.filter(pk=instance.pk).values('pk')

    return any((
        GardenPlanting.objects.filter(garden_square_id__in=square_ids).exists(),
        GardenSquareDirectSowPlanting.objects.filter(location_id__in=square_ids).exists(),
        SpecificPlantLocation.objects.filter(garden_square_id__in=square_ids).exists(),
        Harvest.objects.filter(garden_square_id__in=square_ids).exists(),
        Harvest.objects.filter(garden_row_id__in=row_ids).exists(),
        GardenRowDirectSowPlanting.objects.filter(location_id__in=row_ids).exists(),
        InputApplicationTarget.objects.filter(garden_square_id__in=square_ids).exists(),
        InputApplicationTarget.objects.filter(garden_row_id__in=row_ids).exists(),
    ))


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
        fields = ['pk', 'area', 'name', 'kind', 'placement_x', 'placement_y', 'size_x', 'size_y']

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


class GardenAreaViewSet(GeometryWriteMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Area
    """
    queryset = GardenArea.objects.prefetch_related('geometry_confirmations')
    serializer_class = GardenAreaSerializer
    pagination_class = None

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


class GardenBedViewSet(GeometryWriteMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Bed
    """
    queryset = GardenBed.objects.all()
    serializer_class = GardenBedSerializer
    pagination_class = None


class GardenRowViewSet(GeometryWriteMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Row
    """
    queryset = GardenRow.objects.all()
    serializer_class = GardenRowSerializer
    pagination_class = None


class GardenSquareViewSet(GeometryWriteMixin, CurrentWorkspaceViewSetMixin, viewsets.ModelViewSet):  # pylint: disable=too-many-ancestors
    """
    ViewSet for Garden Square
    """
    queryset = GardenSquare.objects.all()
    serializer_class = GardenSquareSerializer
    pagination_class = None


router = routers.DefaultRouter()
router.register(r'areas', GardenAreaViewSet)
router.register(r'beds', GardenBedViewSet)
router.register(r'rows', GardenRowViewSet)
router.register(r'squares', GardenSquareViewSet)
