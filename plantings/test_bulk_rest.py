"""REST contract tests for reviewed bulk plant operations."""

from uuid import uuid4

from django.utils import timezone

from locations.models import Location
from tests.api import RESTContractTestCase
from tests.factories import (
    make_location,
    make_seed_tray_cell_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from .models import (
    BulkPlantOperation,
    PlantLifecycleEvent,
    SpecificPlant,
    SpecificPlantLocation,
)


class BulkPlantOperationRESTTests(RESTContractTestCase):
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
