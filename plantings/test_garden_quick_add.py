"""REST workflow tests for source-neutral Garden quick-add."""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from costing.models import CostAllocation
from tests.factories import make_garden_square, make_location, make_plant
from workspaces.models import Workspace, get_current_workspace

from .models import GardenPlanting, SpecificPlant, SpecificPlantLocation


class GardenQuickAddTests(APITestCase):
    """Previewed rows commit together and retain their truthful origin."""

    url = '/plantings/garden-quick-add/'

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='garden-quick-add')
        self.client.force_authenticate(self.user)
        self.client.force_login(self.user)
        self.workspace = get_current_workspace()
        self.crop = make_plant(workspace=self.workspace)
        self.square = make_garden_square(workspace=self.workspace)

    def entry(self, **overrides):
        """Return one minimal reviewed-row payload."""
        payload = {
            'plant': self.crop.pk,
            'new_variety_name': 'Garden selection',
            'source': GardenPlanting.Source.EXISTING_UNKNOWN,
            'tracking': GardenPlanting.Tracking.AGGREGATE,
            'quantity': 3,
            'recorded_on': '2026-08-01',
            'date_basis': GardenPlanting.DateBasis.FIRST_OBSERVED,
            'garden_square': self.square.pk,
        }
        payload.update(overrides)
        return payload

    def create_reviewed(self, entries):
        """Preview and submit the exact review token the API returned."""
        preview = self.client.post(f'{self.url}preview/', {'entries': entries}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        created = self.client.post(
            self.url,
            {'entries': entries, 'confirmation_token': preview.data['confirmation_token']},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        return created

    def test_every_source_can_be_recorded_without_inventory(self):
        """No propagation description implicitly requires a stock record."""
        for index, source in enumerate(GardenPlanting.Source.values):
            with self.subTest(source=source):
                self.create_reviewed([
                    self.entry(source=source, new_variety_name=f'Variety {index}'),
                ])

        self.assertEqual(GardenPlanting.objects.count(), len(GardenPlanting.Source.values))

    def test_individual_entry_creates_named_and_unnamed_plants(self):
        """Quantity serializes plants while names remain optional per plant."""
        location = make_location(workspace=self.workspace)
        response = self.create_reviewed([
            self.entry(
                tracking=GardenPlanting.Tracking.INDIVIDUAL,
                quantity=2,
                garden_square=None,
                location=location.pk,
                individual_names=['Pearl'],
                perennial=True,
            ),
        ])

        origin = GardenPlanting.objects.get(pk=response.data[0]['pk'])
        self.assertTrue(origin.perennial)
        self.assertEqual(list(origin.specific_plants.order_by('pk').values_list('name', flat=True)), ['Pearl', ''])
        self.assertEqual(SpecificPlantLocation.objects.filter(location=location, ended__isnull=True).count(), 2)

    def test_square_projection_reports_aggregate_and_individual_once(self):
        """The garden canvas projection merges quick origins without doubling."""
        self.create_reviewed([self.entry()])
        self.create_reviewed([
            self.entry(
                new_variety_name='Named perennial',
                tracking=GardenPlanting.Tracking.INDIVIDUAL,
                quantity=2,
                individual_names=['One', 'Two'],
                perennial=True,
            ),
        ])

        response = self.client.get('/plantings/garden/squares/current/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['plantings']), 3, response.json())
        self.assertEqual(sum(row['quantity'] for row in response.json()['plantings']), 5)

    def test_manual_purchase_cost_is_allocated_to_each_plant(self):
        """Advanced cost is auditable without a fake inventory receipt."""
        self.create_reviewed([
            self.entry(
                tracking=GardenPlanting.Tracking.INDIVIDUAL,
                quantity=2,
                individual_names=[],
                purchase_cost='12.0000',
            ),
        ])

        layers = CostAllocation.objects.filter(
            source_type=CostAllocation.SourceType.GARDEN_PLANTING,
            reversal_of__isnull=True,
            reversal__isnull=True,
        ).order_by('specific_plant_id')
        self.assertEqual(layers.count(), 2)
        self.assertEqual([layer.amount for layer in layers], [6, 6])

    def test_existing_occupancy_warns_but_can_be_confirmed(self):
        """A reviewed companion planting remains intentional and permitted."""
        self.create_reviewed([self.entry()])
        second = self.entry(new_variety_name='Companion')
        preview = self.client.post(f'{self.url}preview/', {'entries': [second]}, format='json')

        self.assertEqual(preview.status_code, 200)
        self.assertIn('location_occupied', [warning['code'] for warning in preview.data['warnings']])
        created = self.client.post(
            self.url,
            {'entries': [second], 'confirmation_token': preview.data['confirmation_token']},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)

    def test_changed_occupancy_invalidates_a_review(self):
        """A stale clean review cannot acknowledge a warning it never showed."""
        entry = self.entry()
        preview = self.client.post(f'{self.url}preview/', {'entries': [entry]}, format='json')
        self.create_reviewed([self.entry(new_variety_name='Arrived meanwhile')])

        response = self.client.post(
            self.url,
            {'entries': [entry], 'confirmation_token': preview.data['confirmation_token']},
            format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data['warnings'])

    def test_invalid_row_rolls_back_the_whole_import(self):
        """Review rejects the batch before any inline records are created."""
        response = self.client.post(
            f'{self.url}preview/',
            {'entries': [self.entry(), self.entry(quantity=0)]},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GardenPlanting.objects.exists())
        self.assertFalse(SpecificPlant.objects.exists())

    def test_other_workspace_references_are_rejected(self):
        """Nested batch input cannot bypass deployment workspace isolation."""
        other = Workspace.objects.create(name='Other')
        foreign = make_garden_square(workspace=other)

        response = self.client.post(
            f'{self.url}preview/',
            {'entries': [self.entry(garden_square=foreign.pk)]},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('garden_square', response.data[0])

    def test_nursery_profile_cannot_use_the_garden_route(self):
        """The source-neutral shortcut does not widen Nursery lineage."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
