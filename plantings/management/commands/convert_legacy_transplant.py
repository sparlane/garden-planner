"""Convert one legacy aggregate transplant into individual plant history."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plantings.models import (
    GardenSquareTransplant,
    SeedTrayCellPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from workspaces.models import get_current_workspace


class Command(BaseCommand):
    """Preview or apply one explicit legacy-transplant conversion."""

    help = (
        'Convert one GardenSquareTransplant into individual SpecificPlant '
        'location histories. The command is a dry run unless --apply is used.'
    )

    def add_arguments(self, parser):
        parser.add_argument('transplant_id', type=int)
        parser.add_argument(
            '--cell-planting',
            type=int,
            help='Source SeedTrayCellPlanting ID for newly recovered plants.',
        )
        parser.add_argument(
            '--germinated-at',
            help='Timezone-aware ISO-8601 germination time for new plants.',
        )
        parser.add_argument(
            '--existing-plant',
            action='append',
            default=[],
            type=int,
            help=(
                'SpecificPlant ID already included in the aggregate total. '
                'Repeat for each existing plant.'
            ),
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            dest='apply_changes',
            help='Apply the conversion; without this flag, only preview it.',
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            transplant = self._get_transplant(
                options['transplant_id'],
                for_update=options['apply_changes'],
            )
            self._describe_transplant(transplant)
            self._validate_active(transplant)

            if not options['cell_planting'] or not options['germinated_at']:
                raise CommandError(
                    'Choose --cell-planting and --germinated-at, then rerun '
                    'the command. Add --apply only after reviewing the preview.'
                )

            cell_planting = self._get_cell_planting(
                options['cell_planting'],
                transplant,
                for_update=options['apply_changes'],
            )
            germinated_at = self._parse_germinated_at(
                options['germinated_at'],
                transplant,
            )
            existing_plants = self._get_existing_plants(
                options['existing_plant'],
                transplant,
                for_update=options['apply_changes'],
            )
            new_plant_count = self._describe_conversion(
                transplant,
                cell_planting,
                germinated_at,
                existing_plants,
            )

            if not options['apply_changes']:
                self.stdout.write(self.style.WARNING(
                    'Dry run only: no rows were changed. Rerun with --apply '
                    'to perform this conversion.'
                ))
                return

            self._apply_conversion(
                transplant,
                cell_planting,
                germinated_at,
                new_plant_count,
            )
            self.stdout.write(self.style.SUCCESS(
                f'Converted and deleted GardenSquareTransplant '
                f'#{options["transplant_id"]}.'
            ))

    @staticmethod
    def _get_transplant(transplant_id, *, for_update):
        queryset = GardenSquareTransplant.objects.select_related(
            'original_planting__seeds_used__seeds__plant_variety__plant',
            'location__bed__area',
        ).filter(workspace=get_current_workspace())
        if for_update:
            queryset = queryset.select_for_update()
        try:
            return queryset.get(pk=transplant_id)
        except GardenSquareTransplant.DoesNotExist as exc:
            raise CommandError(
                f'GardenSquareTransplant #{transplant_id} does not exist.'
            ) from exc

    def _describe_transplant(self, transplant):
        planting = transplant.original_planting
        variety = planting.seeds_used.seeds.plant_variety
        square = transplant.location
        self.stdout.write(f'Legacy transplant: #{transplant.pk}')
        self.stdout.write(f'Plant: {variety.plant.name}')
        self.stdout.write(f'Variety: {variety.name}')
        self.stdout.write(f'Original planting: #{planting.pk}')
        self.stdout.write(f'Target quantity: {transplant.quantity}')
        self.stdout.write(f'Transplanted at: {transplant.transplanted.isoformat()}')
        self.stdout.write(
            f'Garden: {square.bed.area.name} / {square.bed.name} / {square.name} '
            f'(square #{square.pk})'
        )
        self.stdout.write('Available source allocations:')
        allocations = planting.cell_plantings.select_related(
            'cell__tray__model',
        ).order_by('pk')
        if not allocations:
            self.stdout.write('  none')
        for allocation in allocations:
            cell = allocation.cell
            self.stdout.write(
                f'  #{allocation.pk}: tray #{cell.tray_id} '
                f'({cell.tray.model.identifier}), cell #{cell.pk} '
                f'({cell.x_position}, {cell.y_position}), '
                f'{allocation.quantity} sown, '
                f'{allocation.specific_plants.count()} plants observed'
            )

    @staticmethod
    def _validate_active(transplant):
        if transplant.removed:
            raise CommandError(
                'Removed legacy transplants cannot be converted because no '
                'truthful garden-location end time was recorded.'
            )

    @staticmethod
    def _get_cell_planting(cell_planting_id, transplant, *, for_update):
        queryset = SeedTrayCellPlanting.objects.select_related(
            'cell__tray__model',
        ).filter(
            seed_tray_planting__workspace=transplant.workspace,
        )
        if for_update:
            queryset = queryset.select_for_update()
        try:
            cell_planting = queryset.get(pk=cell_planting_id)
        except SeedTrayCellPlanting.DoesNotExist as exc:
            raise CommandError(
                f'SeedTrayCellPlanting #{cell_planting_id} does not exist.'
            ) from exc
        if cell_planting.seed_tray_planting_id != transplant.original_planting_id:
            raise CommandError(
                f'SeedTrayCellPlanting #{cell_planting_id} does not belong to '
                f'original planting #{transplant.original_planting_id}.'
            )
        return cell_planting

    @staticmethod
    def _parse_germinated_at(value, transplant):
        germinated_at = parse_datetime(value)
        if germinated_at is None or timezone.is_naive(germinated_at):
            raise CommandError(
                '--germinated-at must be a timezone-aware ISO-8601 datetime.'
            )
        planting = transplant.original_planting
        if germinated_at < planting.planted:
            raise CommandError(
                '--germinated-at cannot be before the original sowing time.'
            )
        if germinated_at > transplant.transplanted:
            raise CommandError(
                '--germinated-at cannot be after the transplant time.'
            )
        return germinated_at

    @staticmethod
    def _get_existing_plants(plant_ids, transplant, *, for_update):
        if len(plant_ids) != len(set(plant_ids)):
            raise CommandError('--existing-plant IDs must be unique.')
        if len(plant_ids) > transplant.quantity:
            raise CommandError(
                'Existing plant count cannot exceed the aggregate target quantity.'
            )

        queryset = SpecificPlant.objects.filter(
            pk__in=plant_ids,
            workspace=transplant.workspace,
        )
        if for_update:
            queryset = queryset.select_for_update()
        plants_by_id = {plant.pk: plant for plant in queryset}
        missing_ids = sorted(set(plant_ids) - plants_by_id.keys())
        if missing_ids:
            raise CommandError(
                f'SpecificPlant IDs do not exist: {missing_ids}.'
            )

        plants = [plants_by_id[plant_id] for plant_id in plant_ids]
        for plant in plants:
            if (
                plant.cell_planting.seed_tray_planting_id
                != transplant.original_planting_id
            ):
                raise CommandError(
                    f'SpecificPlant #{plant.pk} does not belong to original '
                    f'planting #{transplant.original_planting_id}.'
                )
            has_matching_history = plant.locations.filter(
                location_type=SpecificPlantLocation.GARDEN_SQUARE,
                garden_square_id=transplant.location_id,
            ).exists()
            if not has_matching_history:
                raise CommandError(
                    f'SpecificPlant #{plant.pk} has no garden history in '
                    f'square #{transplant.location_id}.'
                )
        return plants

    def _describe_conversion(
        self,
        transplant,
        cell_planting,
        germinated_at,
        existing_plants,
    ):
        new_plant_count = transplant.quantity - len(existing_plants)
        cell = cell_planting.cell
        existing_ids = [plant.pk for plant in existing_plants]
        self.stdout.write(
            f'Source: allocation #{cell_planting.pk}, tray #{cell.tray_id}, '
            f'cell #{cell.pk} ({cell.x_position}, {cell.y_position})'
        )
        self.stdout.write(f'Germinated at: {germinated_at.isoformat()}')
        self.stdout.write(f'Existing plants in target: {existing_ids}')
        self.stdout.write(f'New plants to create: {new_plant_count}')
        self.stdout.write(
            f'Legacy aggregate to delete after conversion: #{transplant.pk}'
        )
        return new_plant_count

    @staticmethod
    def _apply_conversion(
        transplant,
        cell_planting,
        germinated_at,
        new_plant_count,
    ):
        recovery_note = (
            f'Recovered from legacy GardenSquareTransplant #{transplant.pk}.'
        )
        for _index in range(new_plant_count):
            plant = SpecificPlant.objects.create(
                workspace=transplant.workspace,
                cell_planting=cell_planting,
                germinated=germinated_at,
                notes=recovery_note,
            )
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.SEED_TRAY_CELL,
                seed_tray_cell=cell_planting.cell,
                started=germinated_at,
                ended=transplant.transplanted,
                notes=recovery_note,
            )
            SpecificPlantLocation.objects.create(
                specific_plant=plant,
                location_type=SpecificPlantLocation.GARDEN_SQUARE,
                garden_square=transplant.location,
                started=transplant.transplanted,
                notes=transplant.notes,
            )
        transplant.delete()
