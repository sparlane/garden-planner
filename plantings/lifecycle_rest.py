"""REST resources and explicit outcome actions for plant lifecycle events.

Events are facts, so the collections are read-only and every change arrives
through a named transition action that appends one auditable row.
"""

# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from workspaces.scoping import CurrentWorkspaceViewSetMixin

from .lifecycle import (
    OUTCOME_EVENTS,
    EventType,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_bulk_outcome,
    record_lifecycle_event,
    reverse_lifecycle_event,
)
from .models import PlantLifecycleEvent, SpecificPlant


def _model_errors(error):
    """Translate model validation errors into DRF response details."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


def _run_domain_action(function, *args, **kwargs):
    """Invoke a lifecycle service with field-friendly API errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


def _outcome_request(event_type, values):
    """Build the service request one validated action payload describes."""
    return OutcomeRequest(
        event_type=event_type,
        occurred_at=values.get('occurred_at'),
        reason=values['reason'],
        reference=values['reference'],
    )


def _action_values(request, serializer_class):
    """Validate one action payload and return its cleaned values."""
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for plant lifecycle actions."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class OutcomeSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate when one outcome happened and what it came from."""

    occurred_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(allow_blank=True, required=False, default='')
    reference = serializers.CharField(
        max_length=255,
        allow_blank=True,
        required=False,
        default='',
    )


class BulkOutcomeSerializer(OutcomeSerializer):  # pylint: disable=abstract-method
    """Validate a selection of plants and the one outcome they share."""

    plants = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )
    event_type = serializers.ChoiceField(
        choices=[(event.value, event.label) for event in OUTCOME_EVENTS],
    )


class ReverseEventSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate which recorded fact was wrong and why.

    The reason answers "why was this recorded in error", not "why did the
    situation change". A plant that genuinely was ready and is now being held
    back uses `hold-back`, which keeps both intervals in the history.
    """

    event = serializers.IntegerField()
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    occurred_at = serializers.DateTimeField(required=False)


class PlantLifecycleEventSerializer(serializers.ModelSerializer):
    """Serialize one immutable lifecycle fact."""

    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = PlantLifecycleEvent
        fields = [
            'pk',
            'plant',
            'batch',
            'event_type',
            'occurred_at',
            'reason',
            'reference',
            'created_by',
            'reversal_of',
            'reversed_by',
            'created',
        ]
        read_only_fields = fields

    def get_reversed_by(self, event):
        """Return the correction that reversed this fact, if one did."""
        reversal = getattr(event, 'reversal', None)
        return None if reversal is None else reversal.pk


class PlantLifecycleSerializerMixin:
    """Resolve one plant's derived lifecycle state for a representation.

    The state is replayed from the plant's events rather than stored, and is
    computed once per plant so a list response reuses its prefetch. Concrete
    serializers declare the matching `SerializerMethodField`s themselves
    because DRF only collects declared fields from serializer bases.
    """

    #: The read-only fields a serializer using this mixin must declare.
    LIFECYCLE_FIELDS = [
        'lifecycle_state',
        'sellable',
        'final_outcome',
        'final_outcome_at',
    ]

    def _summary(self, plant):
        """Derive one plant's lifecycle summary at most once per response."""
        cached = getattr(plant, '_lifecycle_summary', None)
        if cached is None:
            cached = plant_lifecycle_summary(plant)
            setattr(plant, '_lifecycle_summary', cached)
        return cached

    def get_lifecycle_state(self, plant):
        """Return the plant's current derived lifecycle state."""
        return self._summary(plant).state

    def get_sellable(self, plant):
        """Return whether the plant may currently be offered to somebody."""
        return self._summary(plant).sellable

    def get_final_outcome(self, plant):
        """Return the fact that resolved this plant, if one has."""
        return self._summary(plant).final_outcome

    def get_final_outcome_at(self, plant):
        """Return when this plant was resolved, if it has been."""
        return self._summary(plant).final_outcome_at


class PlantOutcomeViewSetMixin:
    """Expose one explicit action per recordable plant outcome.

    Each action returns the appended event rather than the plant, so the
    auditable row is what the caller receives.
    """

    @action(detail=True, methods=['post'])
    def ready(self, request, pk=None):  # pylint: disable=unused-argument
        """Record that this plant is ready for sale or use."""
        return self._record_outcome(request, EventType.READY)

    @action(detail=True, methods=['post'])
    def retain(self, request, pk=None):  # pylint: disable=unused-argument
        """Keep this plant for the operation's own use, where it stands."""
        return self._record_outcome(request, EventType.RETAINED)

    @action(detail=True, methods=['post'])
    def fail(self, request, pk=None):  # pylint: disable=unused-argument
        """Record that this plant did not survive."""
        return self._record_outcome(request, EventType.FAILED)

    @action(detail=True, methods=['post'])
    def cull(self, request, pk=None):  # pylint: disable=unused-argument
        """Record that this plant was deliberately removed."""
        return self._record_outcome(request, EventType.CULLED)

    @action(detail=True, methods=['post'])
    def donate(self, request, pk=None):  # pylint: disable=unused-argument
        """Record that this plant left the operation as a gift."""
        return self._record_outcome(request, EventType.DONATED)

    @action(detail=True, methods=['post'], url_path='finish-harvest')
    def finish_harvest(self, request, pk=None):  # pylint: disable=unused-argument
        """Record the final harvest that ends this plant's cultivation."""
        return self._record_outcome(request, EventType.HARVEST_FINISHED)

    @action(detail=True, methods=['post'], url_path='hold-back')
    def hold_back(self, request, pk=None):  # pylint: disable=unused-argument
        """Take this plant off offer without denying it was ever ready."""
        return self._record_outcome(request, EventType.HELD_BACK)

    @action(detail=True, methods=['post'], url_path='end-retention')
    def end_retention(self, request, pk=None):  # pylint: disable=unused-argument
        """Return a retained plant to production, to be graded ready again."""
        return self._record_outcome(request, EventType.RETENTION_ENDED)

    @action(detail=True, methods=['post'], url_path='reverse-event')
    def reverse_event(self, request, pk=None):  # pylint: disable=unused-argument
        """Correct a mistaken fact by appending its reversal.

        This is for a fact that was never true. Where the fact was true and
        the situation then changed, record the change: `hold-back` withdraws
        stock from offer and `end-retention` returns a retained plant to
        production, and both leave the original fact standing.
        """
        values = _action_values(request, ReverseEventSerializer)
        plant = self.get_object()
        event = get_object_or_404(
            PlantLifecycleEvent,
            pk=values['event'],
            plant=plant,
        )
        correction = _run_domain_action(
            reverse_lifecycle_event,
            event,
            request.user,
            values['reason'],
            occurred_at=values.get('occurred_at'),
        )
        return Response(
            PlantLifecycleEventSerializer(correction).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'], url_path='bulk-outcome')
    def bulk_outcome(self, request):
        """Record one shared outcome as a separate event per selected plant."""
        values = _action_values(request, BulkOutcomeSerializer)
        plant_ids = self._resolve_plant_ids(values['plants'])
        events = _run_domain_action(
            record_bulk_outcome,
            plant_ids,
            request.user,
            _outcome_request(values['event_type'], values),
        )
        return Response(
            PlantLifecycleEventSerializer(events, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    def _resolve_plant_ids(self, requested):
        """Reject a selection naming plants outside the current workspace."""
        wanted = sorted(set(requested))
        known = set(
            self.get_queryset()
            .filter(pk__in=wanted)
            .values_list('pk', flat=True)
        )
        missing = [plant_id for plant_id in wanted if plant_id not in known]
        if missing:
            raise ValidationError({
                'plants': f'No such plants in this workspace: {missing}.',
            })
        return wanted

    def _record_outcome(self, request, event_type):
        """Validate one outcome payload and append the resulting fact."""
        values = _action_values(request, OutcomeSerializer)
        event = _run_domain_action(
            record_lifecycle_event,
            self.get_object(),
            request.user,
            _outcome_request(event_type, values),
        )
        return Response(
            PlantLifecycleEventSerializer(event).data,
            status=status.HTTP_201_CREATED,
        )


class PlantLifecycleEventViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Read the recorded lifecycle history.

    Generic update and delete endpoints are not provided: corrections append a
    reversal through the plant's own action.
    """

    queryset = PlantLifecycleEvent.objects.select_related('reversal')
    serializer_class = PlantLifecycleEventSerializer

    def get_queryset(self):
        """Apply the plant, batch, and event-type filters the screens use."""
        queryset = super().get_queryset()
        for field in ('plant', 'batch'):
            value = self.request.query_params.get(field)
            if value:
                if not value.isdigit():
                    raise ValidationError({field: f'Enter a {field} ID.'})
                queryset = queryset.filter(**{f'{field}_id': int(value)})
        event_type = self.request.query_params.get('event_type')
        if event_type:
            if event_type not in {choice.value for choice in EventType}:
                raise ValidationError({'event_type': 'Select a valid event type.'})
            queryset = queryset.filter(event_type=event_type)
        return queryset


class PlantLifecycleEventByPlantViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Read one plant's chronological lifecycle history."""

    queryset = PlantLifecycleEvent.objects.select_related('reversal')
    serializer_class = PlantLifecycleEventSerializer

    def get_queryset(self):
        """Limit the history to the named plant inside this workspace."""
        plant = get_object_or_404(
            SpecificPlant,
            pk=self.kwargs['specific_plant_pk'],
            workspace=self.get_current_workspace(),
        )
        return super().get_queryset().filter(plant=plant)


def register_lifecycle_routes(router):
    """Attach the lifecycle event resources to the planting API router."""
    router.register(r'lifecycle-events', PlantLifecycleEventViewSet)
    router.register(
        r'specificplants/(?P<specific_plant_pk>[^/.]+)/lifecycle-events',
        PlantLifecycleEventByPlantViewSet,
        basename='specificplant-lifecycle-events',
    )
