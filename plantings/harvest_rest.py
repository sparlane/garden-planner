"""REST resources for recording, reversing, and reporting harvests.

A harvest is posted when it is created, so the collection offers no update or
delete: a mistake is corrected through the explicit reverse action, which keeps
the original record visible while excluding it from every total.
"""

# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from garden.models import GardenRow, GardenSquare
from inventory.models import (
    POSITIVE_DECIMAL,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
)
from inventory.rest_query import parse_date, parse_integer
from workspaces.models import get_current_workspace
from workspaces.scoping import (
    CurrentWorkspaceSerializerMixin,
    CurrentWorkspaceViewSetMixin,
)

from .harvests import (
    HarvestRequest,
    harvest_finished_plant_ids,
    record_harvest,
    reverse_harvest,
)
from .models import HARVEST_UNIT_CHOICES, Harvest, ProductionBatch, SpecificPlant
from .yields import (
    GroupBy,
    harvest_report,
    local_day_bounds,
    workspace_zone,
)


def _model_errors(error):
    """Translate model validation errors into DRF response details."""
    if hasattr(error, 'message_dict'):
        return error.message_dict
    return error.messages


def _run_domain_action(function, *args, **kwargs):
    """Invoke a harvest service with field-friendly API errors."""
    try:
        return function(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(_model_errors(exc)) from exc


class ActionSerializer(serializers.Serializer):
    """Validation-only serializer base for harvest actions."""

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class ReverseHarvestSerializer(ActionSerializer):  # pylint: disable=abstract-method
    """Validate why a recorded harvest should stop counting."""

    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class HarvestSerializer(serializers.ModelSerializer):
    """Serialize one posted or reversed harvest."""

    batch_code = serializers.CharField(source='batch.code', read_only=True)
    variety = serializers.IntegerField(source='batch.variety_id', read_only=True)
    variety_name = serializers.CharField(source='batch.variety.name', read_only=True)
    plant_name = serializers.CharField(source='batch.variety.plant.name', read_only=True)
    location_label = serializers.SerializerMethodField()
    plants = serializers.SerializerMethodField()
    finished_plants = serializers.SerializerMethodField()

    class Meta:
        model = Harvest
        fields = [
            'pk',
            'batch',
            'batch_code',
            'variety',
            'variety_name',
            'plant_name',
            'harvested_at',
            'quantity',
            'unit_code',
            'garden_square',
            'garden_row',
            'location_label',
            'quality_rating',
            'grade',
            'notes',
            'status',
            'posted_at',
            'reversed_at',
            'reverse_reason',
            'created_by',
            'reversed_by',
            'created',
            'plants',
            'finished_plants',
        ]
        read_only_fields = fields

    def get_location_label(self, harvest):
        """Return where this harvest was taken, if anywhere was recorded."""
        location = harvest.garden_square or harvest.garden_row
        return None if location is None else str(location)

    def get_plants(self, harvest):
        """Return the individual plants this harvest is attributed to."""
        return sorted(
            allocation.plant_id
            for allocation in harvest.plant_allocations.all()
        )

    def get_finished_plants(self, harvest):
        """Return the plants this harvest ended, if it ended any."""
        return harvest_finished_plant_ids(harvest)


class HarvestCreateSerializer(
    CurrentWorkspaceSerializerMixin,
    ActionSerializer,
):  # pylint: disable=abstract-method
    """Validate one harvest before the service posts it.

    Not a model serializer: the record is written by the domain service inside
    one transaction and is immutable afterwards, so there is nothing for a
    generic create-and-update contract to do.
    """

    batch = serializers.PrimaryKeyRelatedField(queryset=ProductionBatch.objects.all())
    harvested_at = serializers.DateTimeField()
    quantity = serializers.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        min_value=POSITIVE_DECIMAL,
    )
    unit_code = serializers.ChoiceField(choices=HARVEST_UNIT_CHOICES)
    garden_square = serializers.PrimaryKeyRelatedField(
        queryset=GardenSquare.objects.all(),
        required=False,
        allow_null=True,
    )
    garden_row = serializers.PrimaryKeyRelatedField(
        queryset=GardenRow.objects.all(),
        required=False,
        allow_null=True,
    )
    quality_rating = serializers.IntegerField(
        min_value=1,
        max_value=5,
        required=False,
        allow_null=True,
    )
    grade = serializers.ChoiceField(
        choices=Harvest.Grade.choices,
        required=False,
        default=Harvest.Grade.UNGRADED,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    plants = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )
    finish_plants = serializers.BooleanField(required=False, default=False)
    finish_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
    )

    workspace_field_lookups = {
        'batch': 'workspace',
        'garden_square': 'workspace',
        'garden_row': 'workspace',
    }

    def validate_harvested_at(self, value):
        """Reject a crop picked before it existed.

        Kept out of the model so backfills and fixtures stay free to write a
        record with whatever timestamp the history actually holds.
        """
        if value > timezone.now():
            raise serializers.ValidationError(
                'A harvest cannot be recorded in the future.',
            )
        return value

    def validate(self, attrs):
        """Reject a payload no harvest could describe."""
        if attrs.get('garden_square') and attrs.get('garden_row'):
            raise serializers.ValidationError({
                'garden_row': 'Record a garden square or a garden row, not both.',
            })
        if attrs['finish_plants'] and not attrs['plants']:
            raise serializers.ValidationError({
                'plants': 'Select the plants this harvest finished.',
            })
        return attrs

    def _resolve_plant_ids(self, requested):
        """Reject a selection naming plants outside the current workspace."""
        wanted = sorted(set(requested))
        known = set(
            SpecificPlant.objects
            .filter(workspace=get_current_workspace(), pk__in=wanted)
            .values_list('pk', flat=True)
        )
        missing = [plant_id for plant_id in wanted if plant_id not in known]
        if missing:
            raise serializers.ValidationError({
                'plants': f'No such plants in this workspace: {missing}.',
            })
        return wanted

    def create(self, validated_data):
        """Post the harvest the validated payload describes."""
        return _run_domain_action(
            record_harvest,
            get_current_workspace(),
            self.context['request'].user,
            HarvestRequest(
                batch=validated_data['batch'],
                harvested_at=validated_data['harvested_at'],
                quantity=validated_data['quantity'],
                unit_code=validated_data['unit_code'],
                garden_square=validated_data.get('garden_square'),
                garden_row=validated_data.get('garden_row'),
                quality_rating=validated_data.get('quality_rating'),
                grade=validated_data['grade'],
                notes=validated_data['notes'],
                plant_ids=self._resolve_plant_ids(validated_data['plants']),
                finish_plants=validated_data['finish_plants'],
                finish_reason=validated_data['finish_reason'],
            ),
        )[0]

    def to_representation(self, instance):
        """Answer a create with the same contract a read returns."""
        return HarvestSerializer(instance, context=self.context).data


class HarvestViewSet(
    CurrentWorkspaceViewSetMixin,
    viewsets.ModelViewSet,
):  # pylint: disable=too-many-ancestors
    """Record and read harvests.

    PUT, PATCH, and DELETE are not provided: a harvest is posted when it is
    created, and the reverse action is the only way to stop one counting.
    """

    queryset = Harvest.objects.select_related(
        'batch__variety__plant',
        'garden_square',
        'garden_row',
    ).prefetch_related('plant_allocations')
    serializer_class = HarvestSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    bind_workspace_on_create = False

    def get_serializer_class(self):
        """Validate a create through the action contract, not the read one."""
        if self.action == 'create':
            return HarvestCreateSerializer
        return HarvestSerializer

    def get_queryset(self):
        """Apply the crop, place, status, and period filters the screens use."""
        queryset = super().get_queryset()
        for field, lookup in (
            ('batch', 'batch_id'),
            ('variety', 'batch__variety_id'),
            ('garden_square', 'garden_square_id'),
            ('garden_row', 'garden_row_id'),
        ):
            value = parse_integer(self.request.query_params.get(field), field)
            if value is not None:
                queryset = queryset.filter(**{lookup: value})
        plant = parse_integer(self.request.query_params.get('plant'), 'plant')
        if plant is not None:
            queryset = queryset.filter(plant_allocations__plant_id=plant).distinct()
        harvest_status = self.request.query_params.get('status')
        if harvest_status:
            if harvest_status not in {choice.value for choice in Harvest.Status}:
                raise ValidationError({'status': 'Select a valid harvest status.'})
            queryset = queryset.filter(status=harvest_status)
        return _filter_period(queryset, self.request.query_params)

    def perform_destroy(self, instance):
        """Refuse deletion even if a future route were to offer it."""
        raise serializers.ValidationError({
            'detail': 'Posted harvests cannot be deleted; reverse them instead.',
        })

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):  # pylint: disable=unused-argument
        """Stop a mistaken harvest counting without erasing the record."""
        serializer = ReverseHarvestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        harvest = _run_domain_action(
            reverse_harvest,
            self.get_object(),
            request.user,
            serializer.validated_data['reason'],
        )
        return Response(
            HarvestSerializer(harvest, context={'request': request}).data,
            status=status.HTTP_200_OK,
        )


def _filter_period(queryset, params):
    """Narrow a harvest queryset to one inclusive range of local days.

    The bounds come from the reporting module so the list and the report cut
    days at the same instants and cannot drift apart.
    """
    start, end = local_day_bounds(
        workspace_zone(get_current_workspace()),
        parse_date(params.get('harvested_from'), 'harvested_from'),
        parse_date(params.get('harvested_to'), 'harvested_to'),
    )
    if start is not None:
        queryset = queryset.filter(harvested_at__gte=start)
    if end is not None:
        queryset = queryset.filter(harvested_at__lt=end)
    return queryset


class HarvestReportView(APIView):
    """Report yield grouped by one dimension over an optional period.

    A separate view rather than a collection action: the response describes
    groups of harvests rather than harvests, and its filter vocabulary is not
    the collection's.
    """

    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        """Return one row per group in the requested grouping."""
        group_by = request.query_params.get('group_by', GroupBy.BATCH)
        if group_by not in {choice.value for choice in GroupBy}:
            raise ValidationError({'group_by': 'Select a valid grouping.'})
        params = request.query_params
        return Response(harvest_report(get_current_workspace(), {
            'group_by': group_by,
            'batch': parse_integer(params.get('batch'), 'batch'),
            'variety': parse_integer(params.get('variety'), 'variety'),
            'garden_square': parse_integer(
                params.get('garden_square'),
                'garden_square',
            ),
            'garden_row': parse_integer(params.get('garden_row'), 'garden_row'),
            'harvested_from': parse_date(
                params.get('harvested_from'),
                'harvested_from',
            ),
            'harvested_to': parse_date(params.get('harvested_to'), 'harvested_to'),
        }))


def register_harvest_routes(router):
    """Attach the harvest resources to the planting API router."""
    router.register(r'harvests', HarvestViewSet)
