"""REST contract tests for reviewed bulk plant operations."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.db.models import Count, F
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from applications.models import InputApplication
from applications.services import ApplicationRequest, LineRequest, TargetRequest, create_application_draft, post_application
from costing.models import FillDepartureRecalculation
from costing.services import effective_allocations, reallocate_batch
from inventory.ledger import bulk_balance, unpromised_bulk
from inventory.models import InventoryItem, StockMovement
from inventory.units import UnitCode
from locations.models import Location
from seedtrays.container_fills import clean_empty_fill, open_counted_fill
from seedtrays.models import SeedTrayGeneration
from tests.api import RESTContractTestCase
from tests.factories import (
    make_location,
    make_inventory_item,
    make_numbered_container,
    make_stock_lot,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    record_lifecycle_event,
)
from .bulk_operations import BulkOperationConflict, concrete_request, execute_bulk_operation
from .growth import current_growth
from .models import (
    BulkPlantOperation,
    GrowthStage,
    PlantLifecycleEvent,
    SpecificPlant,
    SpecificPlantLocation,
)


class BulkPlantOperationRESTTests(RESTContractTestCase):  # pylint: disable=too-many-public-methods
    """Confirmed actions retain one result and domain record per plant."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.plants = [make_specific_plant() for _index in range(3)]
        for plant in self.plants:
            make_specific_plant_location(specific_plant=plant)

    def payload(self, action, plants=None, **overrides):
        """Return one complete confirmed request with a fresh identity."""
        values = {
            'idempotency_key': str(uuid4()),
            'action': action,
            'atomicity': BulkPlantOperation.Atomicity.ALL_OR_NOTHING,
            'occurred_at': timezone.now().isoformat(),
            'reason': 'Routine nursery work.',
            'plants': (
                [plant.pk for plant in self.plants]
                if plants is None else plants
            ),
            'selection_source': {'mode': 'ids'},
            'action_payload': {},
        }
        values.update(overrides)
        return values

    def test_preview_reports_mixed_eligibility_without_writing(self):
        """Review identifies conflicts but creates no audit or new facts."""
        record_lifecycle_event(
            self.plants[0],
            self.user,
            OutcomeRequest(EventType.FAILED),
        )
        before = PlantLifecycleEvent.objects.count()
        response = self.client.post(
            '/plantings/bulk-operations/preview/',
            self.payload(BulkPlantOperation.Action.CULL),
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['selected'], 3)
        self.assertEqual(response.data['eligible'], 2)
        self.assertEqual(response.data['conflicts'], 1)
        self.assertEqual(PlantLifecycleEvent.objects.count(), before)
        self.assertFalse(BulkPlantOperation.objects.exists())

    def test_holding_a_selection_back_projects_and_records_growing(self):
        """The preview's resulting state comes from the same map the write does."""
        for plant in self.plants:
            record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        payload = self.payload(
            BulkPlantOperation.Action.HOLD_BACK,
            reason='The whole batch has gone leggy.',
        )
        preview = self.client.post(
            '/plantings/bulk-operations/preview/', payload, format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['eligible'], 3)
        self.assertEqual(
            {row['after']['lifecycle_state'] for row in preview.data['plants']},
            {LifecycleState.GROWING},
        )
        response = self.client.post(
            '/plantings/bulk-operations/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                event_type=EventType.HELD_BACK,
            ).count(),
            3,
        )

    def test_a_bulk_backward_action_without_a_reason_conflicts_on_review(self):
        """The missing reason surfaces in the preview, not at apply time."""
        for plant in self.plants:
            record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        payload = self.payload(BulkPlantOperation.Action.HOLD_BACK, reason='')
        response = self.client.post(
            '/plantings/bulk-operations/preview/', payload, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['eligible'], 0)
        self.assertEqual(response.data['conflicts'], 3)
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                event_type=EventType.HELD_BACK,
            ).exists(),
        )

    def test_all_or_nothing_conflict_applies_and_audits_nothing(self):
        """A rejected confirmed attempt is not retained as an operation."""
        record_lifecycle_event(
            self.plants[0],
            self.user,
            OutcomeRequest(EventType.FAILED),
        )
        response = self.client.post(
            '/plantings/bulk-operations/',
            self.payload(BulkPlantOperation.Action.CULL),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['conflicts'], 1)
        self.assertFalse(BulkPlantOperation.objects.exists())
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                plant__in=self.plants[1:],
                event_type=EventType.CULLED,
            ).exists(),
        )

    def test_eligible_only_records_applied_and_skipped_results(self):
        """Mixed work remains reviewable without losing per-plant history."""
        record_lifecycle_event(
            self.plants[0],
            self.user,
            OutcomeRequest(EventType.FAILED),
        )
        response = self.client.post(
            '/plantings/bulk-operations/',
            self.payload(
                BulkPlantOperation.Action.CULL,
                atomicity=BulkPlantOperation.Atomicity.ELIGIBLE_ONLY,
            ),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        results = {entry['plant']: entry for entry in response.data['results']}
        self.assertEqual(results[self.plants[0].pk]['status'], 'skipped')
        for plant in self.plants[1:]:
            self.assertEqual(results[plant.pk]['status'], 'applied')
            self.assertIsNotNone(results[plant.pk]['lifecycle_event'])
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(event_type=EventType.CULLED).count(),
            2,
        )

    def test_an_identical_retry_replays_but_changed_input_is_rejected(self):
        """A lost response cannot double-post or repurpose its request key."""
        payload = self.payload(BulkPlantOperation.Action.READY)
        first = self.client.post('/plantings/bulk-operations/', payload, format='json')
        second = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(BulkPlantOperation.objects.count(), 1)
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(event_type=EventType.READY).count(),
            3,
        )

        payload['reason'] = 'Different work.'
        changed = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(changed.status_code, 400)
        self.assertIn('idempotency_key', changed.data)

    def test_move_preview_allocates_the_last_capacity_deterministically(self):
        """Eligible-only review chooses plants by ID when only some fit."""
        bench = make_location(
            location_type=Location.LocationType.BENCH,
            capacity_basis=Location.CapacityBasis.PLANTS,
            capacity_value=1,
        )
        payload = self.payload(
            BulkPlantOperation.Action.MOVE,
            atomicity=BulkPlantOperation.Atomicity.ELIGIBLE_ONLY,
            action_payload={
                'location_type': SpecificPlantLocation.LOCATION,
                'location': bench.pk,
            },
        )
        preview = self.client.post(
            '/plantings/bulk-operations/preview/',
            payload,
            format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['eligible'], 1)
        eligible = [row['plant'] for row in preview.data['plants'] if row['eligible']]
        self.assertEqual(eligible, [min(plant.pk for plant in self.plants)])

        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant__in=self.plants,
                location=bench,
                ended__isnull=True,
            ).count(),
            1,
        )

    def test_a_selection_can_be_moved_into_one_numbered_pot(self):
        """Three bulbs in one pot are three placements naming one container.

        Nothing constrains one plant per place, only one place per plant, so a
        shared pot is the same review as a shared bench — with no capacity to
        allocate, because the pot itself is what occupies the bench.
        """
        pot = make_numbered_container()
        payload = self.payload(
            BulkPlantOperation.Action.MOVE,
            action_payload={
                'location_type': SpecificPlantLocation.CONTAINER_UNIT,
                'container_unit': pot.pk,
            },
        )
        preview = self.client.post(
            '/plantings/bulk-operations/preview/',
            payload,
            format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['eligible'], 3)
        self.assertEqual(
            preview.data['plants'][0]['after']['location_type'],
            SpecificPlantLocation.CONTAINER_UNIT,
        )

        response = self.client.post('/plantings/bulk-operations/', payload, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant__in=self.plants,
                container_unit=pot,
                ended__isnull=True,
            ).count(),
            3,
        )

    def test_bulk_germination_creates_independent_plants_and_facts(self):
        """A quantity observation remains individual from its first record."""
        allocation = make_seed_tray_cell_planting(quantity=2)
        payload = self.payload(
            BulkPlantOperation.Action.GERMINATE,
            plants=[],
            action_payload={
                'cell_planting': allocation.pk,
                'quantity': 3,
                'notes': 'Multigerm cluster.',
            },
        )
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        plant_ids = [entry['plant'] for entry in response.data['results']]
        self.assertEqual(len(plant_ids), 3)
        self.assertEqual(
            SpecificPlant.objects.filter(pk__in=plant_ids).count(),
            3,
        )
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                plant_id__in=plant_ids,
                event_type=EventType.GERMINATED,
            ).count(),
            3,
        )
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant_id__in=plant_ids,
                ended__isnull=True,
            ).count(),
            3,
        )

    def test_multi_cell_germination_supports_different_counts_per_cell(self):
        """One action preserves each seedling's origin and observed cell count."""
        first = make_seed_tray_cell_planting(quantity=2)
        second = make_seed_tray_cell_planting(
            seed_tray_planting=first.seed_tray_planting,
            cell=make_seed_tray_cell(
                tray=first.seed_tray_planting.seed_tray,
                x_position=1,
            ),
            quantity=2,
        )
        payload = self.payload(
            BulkPlantOperation.Action.GERMINATE,
            plants=[],
            action_payload={
                'germinations': [
                    {'cell_planting': second.pk, 'quantity': 2},
                    {'cell_planting': first.pk, 'quantity': 1},
                ],
                'notes': 'Tray check.',
            },
        )

        preview = self.client.post(
            '/plantings/bulk-operations/preview/', payload, format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], 3)
        self.assertEqual(
            preview.data['source']['germinations'],
            [
                {'cell_planting': second.pk, 'quantity': 2},
                {'cell_planting': first.pk, 'quantity': 1},
            ],
        )

        response = self.client.post(
            '/plantings/bulk-operations/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        plant_ids = [result['plant'] for result in response.data['results']]
        plants = SpecificPlant.objects.filter(pk__in=plant_ids)
        self.assertEqual(plants.count(), 3)
        self.assertEqual(
            list(
                plants.values('cell_planting_id')
                .annotate(count=Count('pk'))
                .order_by('cell_planting_id')
                .values_list('cell_planting_id', 'count')
            ),
            sorted([(first.pk, 1), (second.pk, 2)]),
        )
        self.assertEqual(
            SpecificPlantLocation.objects.filter(
                specific_plant__in=plants,
                seed_tray_cell_id=F('specific_plant__cell_planting__cell_id'),
            ).count(),
            3,
        )

    def test_garden_profile_can_preview_and_record_cell_germination(self):
        """The shared seed-tray screen can germinate plants in Garden mode."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save(update_fields=['mode'])
        allocation = make_seed_tray_cell_planting(quantity=1)
        payload = self.payload(
            BulkPlantOperation.Action.GERMINATE,
            plants=[],
            action_payload={
                'cell_planting': allocation.pk,
                'quantity': 1,
                'notes': '',
            },
        )

        preview = self.client.post(
            '/plantings/bulk-operations/preview/', payload, format='json',
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['eligible'], 1)

        response = self.client.post(
            '/plantings/bulk-operations/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data['results']), 1)

    def test_garden_profile_cannot_use_nursery_bulk_actions(self):
        """The germination exception does not expose other bulk nursery work."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save(update_fields=['mode'])

        response = self.client.post(
            '/plantings/bulk-operations/preview/',
            self.payload(BulkPlantOperation.Action.CULL),
            format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)

    def test_reviewed_stage_update_records_one_shared_observation(self):
        """Bulk stage work links every independently audited result to its fact."""
        stage = GrowthStage.objects.get(workspace=self.workspace, code='rooted')
        payload = self.payload(
            BulkPlantOperation.Action.STAGE,
            action_payload={'stage': stage.pk, 'notes': 'Roots visible.'},
        )
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        observation_ids = {row['nursery_observation'] for row in response.data['results']}
        self.assertEqual(len(observation_ids), 1)
        self.assertTrue(all(current_growth(plant)['stage'] == stage for plant in self.plants))

    def repot_payload(self, count=3):
        """Choose an already filled lot of anonymous pots."""
        location = make_location()
        item = make_inventory_item(
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH, container_size_label='P9',
            container_footprint_m2='0.008100',
        )
        lot = make_stock_lot(item=item, location=location, quantity='10', base_unit_cost=Decimal('3'))
        fill = open_counted_fill(self.workspace, self.user, lot, location, count)
        return fill, self.payload(BulkPlantOperation.Action.REPOT, action_payload={'container_fill': fill.pk})

    def test_repot_preview_rolls_back_and_confirmation_lends_pots(self):
        """A reviewed, retried repot records placements without consuming pots."""
        fill, payload = self.repot_payload()
        original = list(SpecificPlantLocation.objects.values_list('pk', 'ended'))
        preview = self.client.post('/plantings/bulk-operations/preview/', payload, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(list(SpecificPlantLocation.objects.values_list('pk', 'ended')), original)
        self.assertFalse(fill.plant_locations.exists())
        self.assertFalse(BulkPlantOperation.objects.exists())
        for expected_status in (201, 200):
            response = self.client.post('/plantings/bulk-operations/', payload, format='json')
            self.assertEqual(response.status_code, expected_status, response.data)
        self.assertFalse(InputApplication.objects.exists())
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.MovementType.CONSUMPTION).exists())
        self.assertEqual(fill.plant_locations.count(), 3)
        self.assertEqual(bulk_balance(fill.stock_lot, fill.source_location), 10)
        self.assertEqual(unpromised_bulk(fill.stock_lot, fill.source_location), 7)
        self.assertEqual(current_growth(self.plants[0])['container_count'], 1)
        operation = BulkPlantOperation.objects.get()
        self.assertEqual(operation.results.filter(location__container_fill=fill, nursery_observation=None).count(), 3)

    def test_repot_on_releases_empty_pots_without_consuming_destination_pots(self):
        """A second repot releases the original claim without adding pot costs."""
        first, payload = self.repot_payload()
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        second, payload = self.repot_payload()
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(first.plant_locations.filter(ended__isnull=False).count(), 3)
        self.assertEqual(second.plant_locations.filter(ended__isnull=True).count(), 3)
        self.assertEqual(unpromised_bulk(first.stock_lot, first.source_location), 10)
        self.assertEqual(unpromised_bulk(second.stock_lot, second.source_location), 7)
        self.assertFalse(InputApplication.objects.exists())

    def test_repot_refuses_insufficient_or_closed_fills_without_partial_moves(self):
        """Preview and confirmation both validate the whole selection again."""
        fill, payload = self.repot_payload(count=2)
        for endpoint in ('preview/', ''):
            response = self.client.post(f'/plantings/bulk-operations/{endpoint}', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(fill.plant_locations.exists())
        self.assertFalse(BulkPlantOperation.objects.exists())
        clean_empty_fill(self.workspace, self.user, fill, reason='Wrong fill.')
        payload['plants'] = [self.plants[0].pk]
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(fill.plant_locations.exists())

    def test_repot_rechecks_a_fill_closed_after_review(self):
        """A successful preview cannot authorize planting into a later clean."""
        fill, payload = self.repot_payload()
        response = self.client.post('/plantings/bulk-operations/preview/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        clean_empty_fill(self.workspace, self.user, fill, reason='Cleaned meanwhile.')
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(BulkPlantOperation.objects.exists())

    def test_repot_media_departure_preserves_legacy_container_layers(self):
        """Fill departures add mix once while old pot charges keep their identities."""
        first, payload = self.repot_payload()
        plant = self.plants[0]
        legacy = create_application_draft(self.workspace, self.user, ApplicationRequest(
            timezone.now(), first.source_location, lines=(LineRequest(
                first.stock_lot.item, first.stock_lot, '1', 'each',
                targets=(TargetRequest('specific_plant', plant),),
            ),),
        ))
        post_application(legacy, self.user)
        reallocate_batch(plant.batch, self.user, 'manual_recalculate')
        original = [(row.pk, row.amount) for row in effective_allocations(plant.batch)]
        self.assertTrue(original)
        media = make_stock_lot(location=first.source_location, base_unit_cost=Decimal('2'))
        application = create_application_draft(self.workspace, self.user, ApplicationRequest(
            timezone.now(), first.source_location, lines=(LineRequest(
                media.item, media, '6', 'l', usage_basis='manual',
                targets=(TargetRequest('container_fill', first),),
            ),),
        ))
        post_application(application, self.user)
        # Apply media before the planting time recorded in the operation.
        payload['occurred_at'] = timezone.now().isoformat()
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        _second, payload = self.repot_payload()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post('/plantings/bulk-operations/preview/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(callbacks, [])
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        self.assertEqual([(row.pk, row.amount) for row in effective_allocations(plant.batch)], original)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        layers = effective_allocations(plant.batch)
        self.assertEqual([(row.pk, row.amount) for row in layers if row.application_line_id == legacy.lines.get().pk], original)
        mix, = [row for row in layers if row.application_line_id == application.lines.get().pk]
        self.assertEqual(mix.base_quantity, 2)
        self.assertEqual(mix.amount, 4)
        self.assertFalse(FillDepartureRecalculation.objects.exists())

    def test_repot_rejects_foreign_fills_and_legacy_stock_payloads(self):
        """Neither another workspace nor an old client can consume pots here."""
        fill, payload = self.repot_payload()
        payload['action_payload']['application'] = {'lines': []}
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        payload['action_payload'].pop('application')
        other = Workspace.objects.create(name='Other nursery')
        SeedTrayGeneration.objects.filter(pk=fill.pk).update(workspace=other)
        response = self.client.post('/plantings/bulk-operations/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(BulkPlantOperation.objects.exists())


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentBulkPlantOperationTests(TransactionTestCase):
    """Overlapping confirmed work serializes at its plant and capacity locks."""

    def _post_teardown(self):
        """Restore the configured workspace removed by transactional flushing."""
        super()._post_teardown()
        Workspace.objects.get_or_create(
            pk=settings.CURRENT_WORKSPACE_ID,
            defaults={'name': 'My Garden'},
        )

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='bulk-racer')
        self.plants = [make_specific_plant() for _index in range(2)]
        for plant in self.plants:
            make_specific_plant_location(specific_plant=plant)
        self.bench = make_location(
            location_type=Location.LocationType.BENCH,
            capacity_basis=Location.CapacityBasis.PLANTS,
            capacity_value=1,
        )

    def _execute_move(self, plant_id):
        """Move one plant from an independent database connection."""
        close_old_connections()
        workspace = Workspace.objects.get(pk=self.workspace.pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        destination = Location.objects.get(pk=self.bench.pk)
        request = concrete_request(
            idempotency_key=uuid4(),
            action=BulkPlantOperation.Action.MOVE,
            atomicity=BulkPlantOperation.Atomicity.ELIGIBLE_ONLY,
            occurred_at=timezone.now(),
            reason='Race for the last space.',
            plants=[plant_id],
            selection_source={'mode': 'ids'},
            action_payload={
                'location_type': SpecificPlantLocation.LOCATION,
                'location': destination,
            },
        )
        try:
            execute_bulk_operation(workspace, user, request)
        except BulkOperationConflict:
            result = 'rejected'
        else:
            result = 'applied'
        close_old_connections()
        return result

    def test_only_one_overlapping_move_takes_the_last_space(self):
        """Capacity is rechecked under a shared deterministic location lock."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._execute_move, self.plants[0].pk),
                    pool.submit(self._execute_move, self.plants[1].pk),
                ]
            )

        self.assertEqual(results, ['applied', 'rejected'])
        self.assertEqual(BulkPlantOperation.objects.count(), 1)
        destination_occupants = SpecificPlantLocation.objects.filter(
            location=self.bench,
            ended__isnull=True,
        )
        self.assertEqual(destination_occupants.count(), 1)
