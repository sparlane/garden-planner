"""Pot fill endpoints preserve stock, audit history, and workspace boundaries."""
# pylint: disable=duplicate-code

from rest_framework.test import APIClient

from applications.services import post_application
from inventory.ledger import physical_balance
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import make_inventory_item, make_location, make_seed_tray_generation, make_specific_plant, make_stock_lot
from workspaces.models import Workspace

from .container_fills import open_counted_fill
from .models import SeedTrayGeneration
from .test_pot_media import PotMediaMixin


class ContainerFillRESTTests(PotMediaMixin, CountedStockTestCase):
    """Exercise operator workflows through authenticated action endpoints."""

    base = '/seedtrays/container-fills/'

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def action(self, name, data=None, fill=None):
        """Post one action on the selected fill."""
        return self.client.post(f'{self.base}{(fill or self.fill).pk}/{name}/', data or {}, format='json')

    def contents(self, fill=None):
        """Read the clean preview and its stable decimal representation."""
        response = self.client.get(f'{self.base}{(fill or self.fill).pk}/contents/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_open_counted_and_numbered_fills_without_consuming_containers(self):
        """Both target shapes open their existing stock claim through one route."""
        unit = self.number(self.pots, 1)[0]
        numbered = self.client.post(self.base, {'inventory_unit': unit.pk}, format='json')
        self.assertEqual(numbered.status_code, 201, numbered.data)
        self.assertEqual(numbered.data['container_count'], 1)
        counted = self.client.post(self.base, {
            'stock_lot': self.pots.pk, 'source_location': self.store.pk, 'container_count': 3,
        }, format='json')
        self.assertEqual(counted.status_code, 201, counted.data)
        self.assertIsNone(counted.data['inventory_unit'])
        self.assertEqual(counted.data['created_by'], self.user.pk)
        self.assertEqual(physical_balance(self.pots, self.store), 100)
        self.assertEqual(self.pots.serialized_units.count(), 1)

    def test_partial_potting_clean_and_correction_round_trip(self):
        """A plant retains its share while the remaining mix is reclaimed and restored."""
        self.fill = open_counted_fill(self.workspace, self.user, self.pots, self.store, 3)
        post_application(self.draft(), self.user)
        plant = make_specific_plant()
        response = self.action('plant', {'plants': [plant.pk]})
        self.assertEqual(response.status_code, 201, response.data)
        placement = response.data[0]
        self.assertEqual(placement['container_fill'], self.fill.pk)
        self.assertIsNone(placement['container_unit'])
        self.assertEqual(self.contents()['plants'], [plant.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'/plantings/specificplantlocations/{placement["pk"]}/end/')
        self.assertEqual(response.status_code, 200, response.data)
        preview = self.contents()
        self.assertEqual(preview['media'][0]['base_quantity'], '33.333333333')
        response = self.action('clean', {
            'reason': 'Wash the unused pots.', 'digest': preview['digest'],
            'media': [{'lot': self.media.pk, 'quantity': preview['media'][0]['base_quantity'],
                       'disposition': 'reclaimed', 'reason': 'Reusable.', 'destination': self.store.pk}],
        })
        self.assertEqual(response.status_code, 200, response.data)
        residual = response.data['residuals'][0]
        self.assertIsNone(residual['correction_event'])
        self.assertEqual(self.contents()['media'], [])
        response = self.action('reopen', {'reason': 'Wrong pots.'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data['residuals'][0]['correction_event'])
        self.assertEqual(response.data['residuals'][0]['pk'], residual['pk'])
        self.assertEqual(self.contents()['costs']['departed_cost'], preview['costs']['departed_cost'])

    def test_clean_requires_a_current_digest_and_complete_dispositions(self):
        """A missing or stale confirmation cannot discard newly posted media."""
        stale = self.contents()['digest']
        post_application(self.draft(), self.user)
        for data in ({'reason': 'Clean'}, {'reason': 'Clean', 'digest': stale}):
            response = self.action('clean', data)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.fill.residuals.exists())

    def test_invalid_identity_shapes_and_counts_are_refused(self):
        """Identity and quantity branches cannot silently override each other."""
        unit = self.number(self.pots, 1)[0]
        for data in ({}, {'stock_lot': self.pots.pk},
                     {'inventory_unit': unit.pk, 'stock_lot': self.pots.pk},
                     {'inventory_unit': unit.pk, 'container_count': 2},
                     {'stock_lot': self.pots.pk, 'source_location': self.store.pk, 'container_count': '1.5'}):
            response = self.client.post(self.base, data, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_foreign_fills_and_targets_are_not_available(self):
        """Read and write routes enforce the deployment workspace boundary."""
        other = Workspace.objects.create(name='Other')
        location = make_location(workspace=other)
        item = make_inventory_item(workspace=other, category='pot_container', tracking_mode='mixed', base_unit='each')
        lot = make_stock_lot(workspace=other, item=item, location=location, quantity='5')
        fill = open_counted_fill(other, None, lot, location, 5)
        self.assertEqual(self.client.get(f'{self.base}{fill.pk}/').status_code, 404)
        self.assertEqual(self.action('reopen', {'reason': 'Wrong fill'}, fill).status_code, 404)
        response = self.client.post(self.base, {'stock_lot': lot.pk, 'source_location': location.pk, 'container_count': 1}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.base, {'stock_lot': self.pots.pk, 'source_location': location.pk, 'container_count': 1}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        foreign = make_specific_plant(workspace=other)
        response = self.action('plant', {'plants': [make_specific_plant().pk, foreign.pk]})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.fill.plant_locations.exists())

    def test_trays_authentication_and_history_edits_are_refused(self):
        """Pot actions cannot reach trays or rewrite existing fill history."""
        tray_fill = make_seed_tray_generation()
        self.assertEqual(self.client.get(f'{self.base}{tray_fill.pk}/').status_code, 404)
        detail = f'{self.base}{self.fill.pk}/'
        self.assertEqual(self.client.patch(detail, {'notes': 'Rewrite'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.base).status_code, 403)

    def test_list_filters_and_duplicate_plant_selections(self):
        """Queries name a saved target; malformed filters and duplicate selections fail."""
        response = self.client.get(self.base, {'stock_lot': self.pots.pk, 'status': 'open'})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertEqual([row['pk'] for row in rows], [self.fill.pk])
        for query in ({'stock_lot': 'bad'}, {'status': 'invalid'}):
            self.assertEqual(self.client.get(self.base, query).status_code, 400)
        plant = make_specific_plant()
        self.assertEqual(self.action('plant', {'plants': [plant.pk, plant.pk]}).status_code, 400)
        self.assertEqual(SeedTrayGeneration.objects.filter(pk=self.fill.pk).get().status, 'open')

    def test_bulk_planting_rejects_numbered_fills_and_over_capacity_selections(self):
        """The bulk action cannot invent sharing or partially fill a short buffer."""
        unit = self.number(self.pots, 1)[0]
        response = self.client.post(self.base, {'inventory_unit': unit.pk}, format='json')
        numbered = SeedTrayGeneration.objects.get(pk=response.data['pk'])
        plants = [make_specific_plant(), make_specific_plant()]
        self.assertEqual(self.action('plant', {'plants': [plants[0].pk]}, numbered).status_code, 400)
        counted = open_counted_fill(self.workspace, self.user, self.pots, self.store, 1)
        self.assertEqual(self.action('plant', {'plants': [plant.pk for plant in plants]}, counted).status_code, 400)
        self.assertFalse(counted.plant_locations.exists())
