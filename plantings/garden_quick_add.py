"""Preview and atomically record source-neutral household garden plantings."""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from garden.models import GardenSquare
from locations.models import Location, location_full_name
from locations.occupancy import check_capacity, plant_contribution
from plants.models import Plant, PlantVariety
from seeds.models import SeedPacket
from supplies.models import Supplier
from workspaces.models import Workspace
from workspaces.scoping import CurrentWorkspaceViewSetMixin, RequireWorkspaceModeMixin

from .batches import BatchRequest, create_and_activate_batch
from .models import GardenPlanting, ProductionBatch, SpecificPlant, SpecificPlantLocation
from .sowing import post_sowing_consumption


TOKEN_SALT = 'garden-quick-add-review'
TOKEN_MAX_AGE = 3600


class GardenQuickAddEntrySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate one staged row without writing its inline variety or batch."""

    plant = serializers.PrimaryKeyRelatedField(queryset=Plant.objects.all())
    variety = serializers.PrimaryKeyRelatedField(queryset=PlantVariety.objects.all(), required=False, allow_null=True)
    new_variety_name = serializers.CharField(max_length=1024, required=False, allow_blank=False)
    batch = serializers.PrimaryKeyRelatedField(queryset=ProductionBatch.objects.all(), required=False, allow_null=True)
    source = serializers.ChoiceField(choices=GardenPlanting.Source.choices)
    tracking = serializers.ChoiceField(choices=GardenPlanting.Tracking.choices)
    quantity = serializers.IntegerField(min_value=1)
    quantity_is_approximate = serializers.BooleanField(default=False)
    recorded_on = serializers.DateField()
    date_basis = serializers.ChoiceField(choices=GardenPlanting.DateBasis.choices)
    date_is_approximate = serializers.BooleanField(default=False)
    perennial = serializers.BooleanField(default=False)
    garden_square = serializers.PrimaryKeyRelatedField(queryset=GardenSquare.objects.all(), required=False, allow_null=True)
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all(), required=False, allow_null=True)
    seed_packet = serializers.PrimaryKeyRelatedField(queryset=SeedPacket.objects.all(), required=False, allow_null=True)
    seed_quantity_used = serializers.DecimalField(max_digits=24, decimal_places=9, min_value=Decimal('0.000000001'), required=False)
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all(), required=False, allow_null=True)
    purchase_cost = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0, required=False)
    individual_names = serializers.ListField(child=serializers.CharField(max_length=255, allow_blank=True), required=False, default=list)
    override_reason = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):  # pylint: disable=too-many-branches
        """Enforce workspace ownership and cross-field quick-add rules."""
        workspace = self.context['workspace']
        errors = {}
        for field in ('plant', 'variety', 'batch', 'garden_square', 'location', 'seed_packet', 'supplier'):
            value = attrs.get(field)
            if value is not None and value.workspace_id != workspace.pk:
                errors[field] = 'This record belongs to a different workspace.'
        variety = attrs.get('variety')
        new_name = attrs.get('new_variety_name')
        if bool(variety) == bool(new_name):
            errors['variety'] = 'Select an existing variety or enter one new variety name.'
        if variety and variety.plant_id != attrs['plant'].pk:
            errors['variety'] = 'The variety belongs to a different crop.'
        batch = attrs.get('batch')
        if batch:
            if new_name:
                errors['batch'] = 'A new variety needs a new planting cycle.'
            elif batch.variety_id != getattr(variety, 'pk', None):
                errors['batch'] = 'The planting cycle grows a different variety.'
            elif batch.status != ProductionBatch.Status.ACTIVE:
                errors['batch'] = 'Only an active planting cycle can receive a current planting.'
        if bool(attrs.get('garden_square')) == bool(attrs.get('location')):
            errors['location'] = 'Select exactly one garden square or location.'
        if attrs['tracking'] == GardenPlanting.Tracking.INDIVIDUAL and attrs['quantity_is_approximate']:
            errors['quantity_is_approximate'] = 'Individual plant quantities must be exact.'
        names = attrs['individual_names']
        if attrs['tracking'] == GardenPlanting.Tracking.AGGREGATE and any(names):
            errors['individual_names'] = 'Names apply only to individually tracked plants.'
        elif len(names) > attrs['quantity']:
            errors['individual_names'] = 'Provide no more names than the plant quantity.'
        packet = attrs.get('seed_packet')
        seed_quantity = attrs.get('seed_quantity_used')
        if bool(packet) != bool(seed_quantity is not None):
            errors['seed_packet'] = 'Provide a seed packet and exact quantity together.'
        if packet and attrs['source'] not in {GardenPlanting.Source.DIRECT_SEED, GardenPlanting.Source.INDOOR_RAISED_SEED}:
            errors['seed_packet'] = 'Seed packets apply only to seed propagation sources.'
        if packet and (attrs.get('supplier') or attrs.get('purchase_cost') is not None):
            errors['purchase_cost'] = 'Packet provenance and manual purchase details cannot be combined.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class GardenPlantingReadSerializer(serializers.ModelSerializer):
    """Return enough origin detail for the Garden plantings page."""

    plant = serializers.IntegerField(source='batch.variety.plant_id', read_only=True)
    plant_name = serializers.CharField(source='batch.variety.plant.name', read_only=True)
    variety = serializers.IntegerField(source='batch.variety_id', read_only=True)
    variety_name = serializers.CharField(source='batch.variety.name', read_only=True)
    batch_code = serializers.CharField(source='batch.code', read_only=True)
    location_label = serializers.SerializerMethodField()
    individual_names = serializers.SerializerMethodField()

    class Meta:
        model = GardenPlanting
        fields = [
            'pk', 'plant', 'plant_name', 'variety', 'variety_name', 'batch',
            'batch_code', 'source', 'tracking', 'quantity',
            'quantity_is_approximate', 'recorded_on', 'date_basis',
            'date_is_approximate', 'perennial', 'garden_square', 'location',
            'location_label', 'seed_packet', 'seed_quantity_used', 'supplier',
            'purchase_cost', 'finished_on', 'notes', 'individual_names',
        ]

    def get_location_label(self, entry):
        """Name either kind of supported destination."""
        return str(entry.garden_square) if entry.garden_square_id else location_full_name(entry.location)

    def get_individual_names(self, entry):
        """Keep optional names in stable plant order."""
        return list(entry.specific_plants.order_by('pk').values_list('name', flat=True))


def _legacy_occupant_exists(entry, workspace):
    square = entry.get('garden_square')
    location = entry.get('location')
    if square is not None:
        from .models import GardenSquareDirectSowPlanting  # pylint: disable=import-outside-toplevel

        return GardenSquareDirectSowPlanting.objects.filter(
            workspace=workspace, location=square, removed=False,
        ).exists() or SpecificPlantLocation.objects.filter(
            garden_square=square, ended__isnull=True,
        ).exists()
    return SpecificPlantLocation.objects.filter(location=location, ended__isnull=True).exists()


def _warnings(entries, workspace):
    """Describe possible duplicates and occupants without blocking creation."""
    warnings = []
    for index, entry in enumerate(entries):
        lookup = {field: entry[field] for field in ('garden_square', 'location') if entry.get(field) is not None}
        existing = GardenPlanting.objects.filter(workspace=workspace, finished_on__isnull=True, **lookup)
        same_crop = existing.filter(batch__variety__plant=entry['plant'])
        if entry.get('variety') is not None:
            same_crop = same_crop.filter(batch__variety=entry['variety'])
        if same_crop.exists():
            warnings.append({'entry': index, 'code': 'possible_duplicate', 'message': 'This crop already has a current planting at that location.'})
        if existing.exists() or _legacy_occupant_exists(entry, workspace):
            warnings.append({'entry': index, 'code': 'location_occupied', 'message': 'This location already contains growing plants; companion planting is allowed.'})
    return warnings


def _review_payload(raw_entries, warnings):
    reviewed = {'entries': raw_entries, 'warnings': warnings}
    return {**reviewed, 'confirmation_token': signing.dumps(reviewed, salt=TOKEN_SALT, compress=True)}


class GardenQuickAddViewSet(RequireWorkspaceModeMixin, CurrentWorkspaceViewSetMixin, viewsets.ViewSet):
    """List, preview, and atomically create household garden origins."""

    required_workspace_modes = (Workspace.Mode.GARDEN,)

    def list(self, request):  # pylint: disable=unused-argument
        """Return quick-added origins, leaving task 62 to build the register."""
        entries = GardenPlanting.objects.filter(workspace=self.get_current_workspace()).select_related(
            'batch__variety__plant', 'garden_square__bed__area', 'location',
        ).prefetch_related('specific_plants')
        return Response(GardenPlantingReadSerializer(entries, many=True).data)

    @action(detail=False, methods=['post'])
    def preview(self, request):
        """Validate rows and return the warnings the gardener must review."""
        entries, raw_entries = self._validated_entries(request)
        return Response(_review_payload(raw_entries, _warnings(entries, self.get_current_workspace())))

    def create(self, request):
        """Recheck a signed review and create every row in one transaction."""
        entries, raw_entries = self._validated_entries(request)
        try:
            reviewed = signing.loads(request.data.get('confirmation_token', ''), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        except signing.BadSignature as exc:
            raise serializers.ValidationError({'confirmation_token': 'Preview these entries again.'}) from exc
        warnings = _warnings(entries, self.get_current_workspace())
        if reviewed != {'entries': raw_entries, 'warnings': warnings}:
            return Response(_review_payload(raw_entries, warnings), status=status.HTTP_409_CONFLICT)
        try:
            with transaction.atomic():
                created = [self._create_entry(entry, request.user) for entry in entries]
        except DjangoValidationError as exc:
            errors = exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            raise serializers.ValidationError(errors) from exc
        return Response(GardenPlantingReadSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    def _validated_entries(self, request):
        raw_entries = request.data.get('entries')
        serializer = GardenQuickAddEntrySerializer(data=raw_entries, many=True, context={'workspace': self.get_current_workspace()})
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data:
            raise serializers.ValidationError({'entries': 'Add at least one planting.'})
        return serializer.validated_data, raw_entries

    def _create_entry(self, data, user):  # pylint: disable=too-many-locals
        workspace = self.get_current_workspace()
        variety = data.get('variety') or self._inline_variety(data['plant'], data['new_variety_name'], workspace)
        batch = data.get('batch') or create_and_activate_batch(
            workspace, user,
            BatchRequest(code='', variety=variety, planned_start=data['recorded_on'], notes='Created by Garden quick-add.'),
        )
        values = {key: value for key, value in data.items() if key not in {'plant', 'variety', 'new_variety_name', 'batch', 'individual_names', 'override_reason'}}
        entry = GardenPlanting.objects.create(workspace=workspace, batch=batch, created_by=user, **values)
        started = timezone.make_aware(datetime.combine(entry.recorded_on, time.min), ZoneInfo(workspace.timezone))
        if entry.tracking == GardenPlanting.Tracking.INDIVIDUAL:
            names = data['individual_names']
            if entry.location_id:
                for _index in range(entry.quantity):
                    check_capacity(entry.location, plant_contribution(), data['override_reason'])
            for index in range(entry.quantity):
                plant = SpecificPlant.objects.create(
                    workspace=workspace, garden_planting=entry, germinated=started,
                    name=names[index].strip() if index < len(names) else '', notes=entry.notes,
                )
                SpecificPlantLocation.objects.create(
                    specific_plant=plant,
                    location_type=SpecificPlantLocation.GARDEN_SQUARE if entry.garden_square_id else SpecificPlantLocation.LOCATION,
                    garden_square=entry.garden_square, location=entry.location,
                    started=started, notes=entry.notes, override_reason=data['override_reason'],
                )
        if entry.seed_packet_id:
            post_sowing_consumption(entry, user)
        from costing.services import reallocate_batch  # pylint: disable=import-outside-toplevel

        reallocate_batch(batch, user, 'manual_recalculate')
        return entry

    @staticmethod
    def _inline_variety(plant, name, workspace):
        """Serialize case-insensitive inline creation beneath one locked crop."""
        Plant.objects.select_for_update().get(pk=plant.pk, workspace=workspace)
        existing = PlantVariety.objects.filter(workspace=workspace, plant=plant, name__iexact=name.strip()).order_by('pk').first()
        return existing or PlantVariety.objects.create(workspace=workspace, plant=plant, name=name.strip())


def register_garden_quick_add_routes(router):
    """Attach the source-neutral Garden API to the plantings router."""
    router.register(r'garden-quick-add', GardenQuickAddViewSet, basename='garden-quick-add')
