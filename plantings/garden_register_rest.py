"""Garden-only plant register endpoints."""

# Plain request serializers intentionally have no create/update implementation.
# pylint: disable=abstract-method

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from attachments.rest import AttachmentSerializer
from garden.models import GardenSquare
from locations.models import Location
from workspaces.models import Workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin, RequireWorkspaceModeMixin

from .garden_register import (
    garden_register_projection,
    garden_register_rows,
    garden_register_totals,
    parse_garden_register_filters,
)
from .garden_status import correct_garden_status, finish_garden_planting
from .direct_sown import (
    direct_sown_summary, individualize_direct_sown_crop, move_direct_sown_crop,
    record_direct_sown_event, reverse_direct_sown_event,
)
from .models import DirectSownCropEvent, GardenPlanting, GardenPlantingStatusEvent


class GardenRegisterSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The common shape of an aggregate crop or individual garden plant."""

    key = serializers.CharField(read_only=True)
    record_type = serializers.CharField(read_only=True)
    record_id = serializers.IntegerField(read_only=True)
    plant = serializers.IntegerField(read_only=True)
    plant_name = serializers.CharField(read_only=True)
    variety = serializers.IntegerField(read_only=True)
    variety_name = serializers.CharField(read_only=True)
    batch = serializers.IntegerField(read_only=True)
    batch_code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    source = serializers.CharField(read_only=True)
    state = serializers.CharField(read_only=True)
    quantity = serializers.IntegerField(read_only=True)
    quantity_is_approximate = serializers.BooleanField(read_only=True)
    perennial = serializers.BooleanField(read_only=True)
    container = serializers.BooleanField(read_only=True)
    planted_on = serializers.DateField(read_only=True)
    date_is_approximate = serializers.BooleanField(read_only=True)
    location = serializers.CharField(read_only=True)
    location_label = serializers.CharField(read_only=True)
    expected_harvest_early = serializers.DateField(read_only=True, allow_null=True)
    expected_harvest_late = serializers.DateField(read_only=True, allow_null=True)
    health_flag = serializers.BooleanField(read_only=True)
    next_task = serializers.JSONField(read_only=True, allow_null=True)
    finished_on = serializers.DateField(read_only=True, allow_null=True)


class GardenRegisterPagination(PageNumberPagination):
    """Household-sized pages with whole-filter summaries."""

    page_size = 40
    page_size_query_param = 'page_size'
    max_page_size = 100

    def __init__(self):
        super().__init__()
        self.totals = {}

    def paginate_queryset(self, queryset, request, view=None):
        self.totals = garden_register_totals(queryset)
        return super().paginate_queryset(queryset, request, view=view)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['totals'] = self.totals
        return response


class GardenPlantRegisterViewSet(
    RequireWorkspaceModeMixin,
    CurrentWorkspaceViewSetMixin,
    viewsets.ViewSet,
):
    """List household crops without exposing the nursery availability model."""

    required_workspace_modes = (Workspace.Mode.GARDEN,)
    pagination_class = GardenRegisterPagination

    def list(self, request):
        """Return one filtered page and totals derived from the same rows."""
        rows = garden_register_rows(
            self.get_current_workspace(),
            parse_garden_register_filters(request.query_params),
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        serializer = GardenRegisterSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def retrieve(self, request, pk=None):
        """Return the common identity plus links to its authoritative records."""
        row = next((item for item in garden_register_projection(self.get_current_workspace()) if item['key'] == pk), None)
        if row is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        row['links'] = {
            'garden': '/gardens',
            'batch': f"/plantings/batches/{row['batch']}",
            'plant': f"/plantings/plants/{row['record_id']}" if row['record_type'] == 'individual' else None,
            'harvest': f"/plantings/harvests?batch={row['batch']}",
            'care': '/applications',
            'health': '/health',
            'tasks': row['next_task']['url'] if row['next_task'] else '/work',
        }
        if row['key'].startswith('aggregate-'):
            planting = get_object_or_404(
                GardenPlanting.objects.prefetch_related(
                    'status_events', 'direct_sown_events__reversal',
                    'direct_sown_events__image_attachments',
                ),
                workspace=self.get_current_workspace(), pk=row['record_id'],
            )
            row['origin'] = {
                'seed_packet': planting.seed_packet_id,
                'supplier': planting.supplier_id,
                'purchase_cost': planting.purchase_cost,
                'notes': planting.notes,
            }
            row['history'] = [
                {
                    'id': event.pk, 'type': event.event_type,
                    'occurred_on': event.occurred_on, 'reason': event.reason,
                    'reversal_of': event.reversal_of_id,
                }
                for event in planting.status_events.all()
            ]
            if planting.source == GardenPlanting.Source.DIRECT_SEED:
                summary = direct_sown_summary(planting)
                row['direct_sown_lifecycle'] = DirectSownSummarySerializer(
                    summary, context={'request': request},
                ).data
            else:
                row['direct_sown_lifecycle'] = None
        else:
            row['origin'] = None
            row['history'] = []
            row['direct_sown_lifecycle'] = None
        return Response(row)

    def _aggregate(self, pk):
        if not pk or not pk.startswith('aggregate-'):
            raise serializers.ValidationError({'detail': 'This action applies to an aggregate crop.'})
        try:
            identifier = int(pk.removeprefix('aggregate-'))
        except ValueError as exc:
            raise serializers.ValidationError({'detail': 'Select a valid aggregate crop.'}) from exc
        return get_object_or_404(
            GardenPlanting,
            workspace=self.get_current_workspace(),
            tracking=GardenPlanting.Tracking.AGGREGATE,
            pk=identifier,
        )

    def _direct_aggregate(self, pk):
        planting = self._aggregate(pk)
        if planting.source != GardenPlanting.Source.DIRECT_SEED:
            raise serializers.ValidationError({'detail': 'This action applies to a direct-sown crop.'})
        return planting

    @action(detail=True, methods=['post'], url_path='direct-event')
    def direct_event(self, request, pk=None):
        """Record emergence, retained count, or a reasoned quantity change."""
        payload = DirectSownEventActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            event = record_direct_sown_event(
                self._direct_aggregate(pk), request.user,
                occurred_on=payload.validated_data.get('occurred_on') or timezone.localdate(),
                **{key: value for key, value in payload.validated_data.items() if key != 'occurred_on'},
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(DirectSownEventSerializer(event, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='move-crop')
    def move_crop(self, request, pk=None):
        """Record the aggregate crop moving to another garden location."""
        payload = DirectSownMoveSerializer(
            data=request.data, context={'workspace': self.get_current_workspace()},
        )
        payload.is_valid(raise_exception=True)
        try:
            event = move_direct_sown_crop(
                self._direct_aggregate(pk), request.user,
                occurred_on=payload.validated_data.pop('occurred_on', timezone.localdate()),
                **payload.validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(DirectSownEventSerializer(event).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def individualize(self, request, pk=None):
        """Turn selected aggregate quantity into distinct plant records."""
        payload = DirectSownIndividualizeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            event, plants = individualize_direct_sown_crop(
                self._direct_aggregate(pk), request.user,
                occurred_on=payload.validated_data.pop('occurred_on', timezone.localdate()),
                **payload.validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(
            {'event': DirectSownEventSerializer(event).data, 'plants': [plant.pk for plant in plants]},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='reverse-direct-event')
    def reverse_direct_event(self, request, pk=None):
        """Correct a direct crop fact by appending its reversal."""
        planting = self._direct_aggregate(pk)
        payload = DirectSownReverseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        event = get_object_or_404(
            DirectSownCropEvent, planting=planting, pk=payload.validated_data['event'],
        )
        try:
            reversal = reverse_direct_sown_event(
                event, request.user,
                payload.validated_data.get('occurred_on') or timezone.localdate(),
                payload.validated_data['notes'],
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_errors(exc)) from exc
        return Response(DirectSownEventSerializer(reversal).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def finish(self, request, pk=None):
        """Append a finish or failure instead of silently editing the row."""
        payload = GardenStatusActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            event = finish_garden_planting(
                self._aggregate(pk), request.user,
                payload.validated_data['event_type'],
                payload.validated_data.get('occurred_on') or timezone.localdate(),
                payload.validated_data.get('reason', ''),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return Response(GardenStatusEventSerializer(event).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='correct-status')
    def correct_status(self, request, pk=None):
        """Append a correction for this aggregate's current finish/failure."""
        planting = self._aggregate(pk)
        payload = GardenStatusCorrectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        event = get_object_or_404(
            GardenPlantingStatusEvent,
            pk=payload.validated_data['event'], planting=planting,
        )
        try:
            correction = correct_garden_status(
                event, request.user, payload.validated_data['reason'],
                payload.validated_data.get('occurred_on') or timezone.localdate(),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return Response(GardenStatusEventSerializer(correction).data, status=status.HTTP_201_CREATED)


class GardenStatusActionSerializer(serializers.Serializer):
    """Validate an aggregate finish or failure request."""
    event_type = serializers.ChoiceField(choices=[
        GardenPlantingStatusEvent.EventType.FINISHED,
        GardenPlantingStatusEvent.EventType.FAILED,
    ])
    occurred_on = serializers.DateField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


def _errors(error):
    return error.message_dict if hasattr(error, 'message_dict') else error.messages


class DirectSownEventActionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate one direct-sown quantity fact."""

    event_type = serializers.ChoiceField(choices=[
        choice for choice in DirectSownCropEvent.EventType.choices
        if choice[0] not in {'moved', 'individualized', 'reversed'}
    ])
    occurred_on = serializers.DateField(required=False)
    quantity = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    count_quality = serializers.ChoiceField(
        choices=DirectSownCropEvent.CountQuality.choices, required=False, allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class DirectSownMoveSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate a destination inside the selected workspace."""

    occurred_on = serializers.DateField(required=False)
    garden_square = serializers.PrimaryKeyRelatedField(
        queryset=GardenSquare.objects.all(), required=False, allow_null=True,
    )
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(), required=False, allow_null=True,
    )
    notes = serializers.CharField(allow_blank=False)
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        workspace = self.context['workspace']
        if bool(attrs.get('garden_square')) == bool(attrs.get('location')):
            raise serializers.ValidationError({'location': 'Select exactly one destination.'})
        for field in ('garden_square', 'location'):
            if attrs.get(field) is not None and attrs[field].workspace_id != workspace.pk:
                raise serializers.ValidationError({field: 'The location belongs to another workspace.'})
        return attrs


class DirectSownIndividualizeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate a request for durable plant identities."""

    quantity = serializers.IntegerField(min_value=1)
    occurred_on = serializers.DateField(required=False)
    names = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False, default=list,
    )
    notes = serializers.CharField(allow_blank=False)
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if len(attrs['names']) > attrs['quantity']:
            raise serializers.ValidationError({'names': 'Provide no more names than the quantity.'})
        return attrs


class DirectSownReverseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Require an explanation for a compensating event."""

    event = serializers.IntegerField(min_value=1)
    occurred_on = serializers.DateField(required=False)
    notes = serializers.CharField(allow_blank=False)


class DirectSownEventSerializer(serializers.ModelSerializer):
    """Expose immutable lifecycle history and optional evidence."""

    attachments = AttachmentSerializer(source='image_attachments', many=True, read_only=True)

    class Meta:
        model = DirectSownCropEvent
        fields = [
            'pk', 'event_type', 'occurred_on', 'quantity', 'quantity_delta',
            'count_quality', 'garden_square_before', 'location_before',
            'garden_square_after', 'location_after', 'reversal_of', 'notes',
            'attachments', 'created',
        ]


class DirectSownSummarySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Report seed, plant, loss, harvest, and location figures separately."""

    seeds_sown = serializers.IntegerField()
    emerged_plants = serializers.IntegerField(allow_null=True)
    losses = serializers.DictField()
    loss_quantity = serializers.IntegerField()
    individualized = serializers.IntegerField()
    current_plants = serializers.IntegerField(allow_null=True)
    count_quality = serializers.CharField(allow_null=True)
    state = serializers.CharField()
    garden_square = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    location = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    harvest = serializers.ListField()
    events = DirectSownEventSerializer(many=True)


class GardenStatusCorrectionSerializer(serializers.Serializer):
    """Validate a correction naming the mistaken status event."""
    event = serializers.IntegerField(min_value=1)
    occurred_on = serializers.DateField(required=False)
    reason = serializers.CharField(allow_blank=False)


class GardenStatusEventSerializer(serializers.ModelSerializer):
    """Return the append-only aggregate status fact."""

    class Meta:
        """Expose the event's audit fields."""
        model = GardenPlantingStatusEvent
        fields = ['pk', 'planting', 'event_type', 'occurred_on', 'reason', 'reversal_of', 'created']


def register_garden_register_routes(router):
    """Keep the household route distinct from ``/register/``."""
    router.register(
        r'garden-register', GardenPlantRegisterViewSet,
        basename='garden-plant-register',
    )
