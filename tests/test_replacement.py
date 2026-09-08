"""Replacing a catalog record that has stopped being right about what it names."""

from inventory.models import InventoryItem, QuantityCertainty
from seeds.models import Seeds
from seedtrays.models import SeedTrayModel
from tests.api import RESTContractTestCase
from tests.factories import make_location, make_plant_variety, make_supplier


class SeedCatalogTestCase(RESTContractTestCase):
    """One seed catalog entry, created the way the Seeds screen creates it."""

    variety_name = 'Roma'
    supplier_code = 'ROMA-1'
    #: Whether the entry starts with a packet posted against it, which is what
    #: freezes what it names.
    posted = False

    def setUp(self):
        super().setUp()
        self.supplier = make_supplier(name='Kings Seeds')
        self.variety = make_plant_variety(name=self.variety_name)
        self.seeds_pk = self.create_entry()
        self.packet = self.receive_packet() if self.posted else None

    def create_entry(self, **overrides):
        """Create one seed catalog entry through the public workflow."""
        payload = {
            'supplier': self.supplier.pk,
            'plant_variety': self.variety.pk,
            'supplier_code': self.supplier_code,
            'base_unit': 'seed',
        }
        payload.update(overrides)
        response = self.client.post('/seeds/seeds/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['pk']

    def receive_packet(self, seeds_pk=None):
        """Receive and post one packet, which is what freezes the identity."""
        draft = self.client.post(
            '/seeds/packet-receipts/',
            {
                'seeds': seeds_pk or self.seeds_pk,
                'quantity_certainty': QuantityCertainty.EXACT,
                'quantity': '250',
                'line_price': '4.5000',
                'received_date': '2026-08-02',
            },
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        posted = self.client.post(
            f"/seeds/packet-receipts/{draft.data['pk']}/post/", {}, format='json',
        )
        self.assertEqual(posted.status_code, 201, posted.data)
        return posted.data

    def correct(self, changes, seeds_pk=None):
        """Ask for an ordinary in-place edit of the entry."""
        return self.client.patch(
            f'/seeds/seeds/{seeds_pk or self.seeds_pk}/', changes, format='json',
        )

    def preview(self, seeds_pk=None):
        """Ask what replacing the entry would leave where it is."""
        return self.client.get(
            f'/seeds/seeds/{seeds_pk or self.seeds_pk}/replace/',
        )

    def replace(self, changes, seeds_pk=None):
        """Ask for the replacement itself."""
        return self.client.post(
            f'/seeds/seeds/{seeds_pk or self.seeds_pk}/replace/',
            changes,
            format='json',
        )


class SeedCatalogCorrectionTests(SeedCatalogTestCase):
    """An entry with nothing posted against it is corrected in place."""

    def test_the_supplier_of_an_unused_entry_is_corrected_in_place(self):
        """Until something is posted, a wrong entry is simply wrong."""
        other = make_supplier(name='Egmont Seeds')

        response = self.correct({'supplier': other.pk})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['supplier'], other.pk)
        self.assertIsNone(response.data['replaced_by'])

    def test_the_unit_of_an_unused_entry_is_corrected_in_place(self):
        """The paired item is not measuring anything yet, so it can move."""
        response = self.correct({'base_unit': 'seed_cluster'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['base_unit'], 'seed_cluster')

    def test_a_correction_renames_the_item_it_is_paired_with(self):
        """The catalog and the stock screens name the same packet."""
        other = make_plant_variety(name='Beefsteak')

        self.correct({'plant_variety': other.pk})

        seeds = Seeds.objects.get(pk=self.seeds_pk)
        self.assertEqual(seeds.inventory_item.name, str(seeds))
        self.assertIn('Beefsteak', seeds.inventory_item.name)

    def test_an_unused_entry_reports_its_identity_as_editable(self):
        """The screen asks before it offers to correct or to replace."""
        response = self.preview()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['identity_locked'])
        self.assertEqual(
            response.data['identity_fields'],
            ['supplier', 'plant_variety', 'base_unit'],
        )
        self.assertEqual(response.data['stays'], [])
        self.assertEqual(response.data['blockers'], [])


class SeedCatalogReplacementTests(SeedCatalogTestCase):
    """A posted packet freezes what the entry it was received against says."""

    variety_name = 'Beetroot'
    supplier_code = 'BEET-1'
    posted = True

    def test_a_posted_packet_freezes_the_unit_it_was_received_in(self):
        """Editing it would restate the receipt rather than correct it."""
        response = self.correct({'base_unit': 'seed_cluster'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['base_unit'],
            ['Replace the record instead of changing this after stock has been posted.'],
        )

    def test_a_posted_packet_freezes_the_supplier_and_the_variety(self):
        """All three say what the packet on the shelf is."""
        other = make_supplier(name='Egmont Seeds')

        response = self.correct({'supplier': other.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn('Replace the record instead', response.data['supplier'][0])

    def test_a_description_is_still_editable_after_stock_is_posted(self):
        """Only what the entry *is* freezes; what it says about it does not."""
        response = self.correct({'notes': 'Multigerm; thin after emergence.'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['notes'], 'Multigerm; thin after emergence.')

    def test_a_preview_names_what_would_stay_where_it_is(self):
        """Nobody should have to replace an entry to find out what is on it."""
        response = self.preview()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['identity_locked'])
        self.assertEqual(response.data['blockers'], [])
        relations = {entry['relation'] for entry in response.data['stays']}
        self.assertIn('seeds.seedpacket.seeds', relations)

    def test_a_preview_writes_nothing(self):
        """It is the question, asked before the answer is acted on."""
        self.preview()

        self.assertEqual(Seeds.objects.count(), 1)
        self.assertTrue(Seeds.objects.get(pk=self.seeds_pk).active)

    def test_a_replacement_carries_the_corrected_unit(self):
        """The whole point is an entry that counts the new packets right."""
        response = self.replace({'base_unit': 'seed_cluster'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['replacement']['base_unit'], 'seed_cluster')
        self.assertTrue(response.data['replacement']['active'])

    def test_a_replacement_inherits_everything_it_was_not_asked_to_change(self):
        """A correction names what is wrong, not the whole record again."""
        response = self.replace({'base_unit': 'seed_cluster'})

        replacement = response.data['replacement']
        self.assertEqual(replacement['supplier'], self.supplier.pk)
        self.assertEqual(replacement['plant_variety'], self.variety.pk)
        self.assertEqual(replacement['supplier_code'], 'BEET-1')

    def test_the_replaced_entry_is_retired_pointing_at_its_successor(self):
        """A reader who searches for the old entry finds where it went."""
        response = self.replace({'base_unit': 'seed_cluster'})

        source = response.data['source']
        self.assertFalse(source['active'])
        self.assertEqual(source['replaced_by'], response.data['replacement']['pk'])

    def test_a_replacement_moves_nothing(self):
        """The packet was received as what the old entry said, and still is."""
        response = self.replace({'base_unit': 'seed_cluster'})

        self.assertEqual(
            self.client.get(f"/seeds/packets/all/{self.packet['pk']}/").data['seeds'],
            self.seeds_pk,
        )
        stayed = {entry['relation'] for entry in response.data['stayed']}
        self.assertIn('seeds.seedpacket.seeds', stayed)

    def test_the_posted_packet_keeps_the_unit_it_was_received_in(self):
        """Replacing corrects the catalog, not what the ledger already holds."""
        packet = self.client.get(f"/seeds/packets/all/{self.packet['pk']}/")
        received_in = packet.data['inventory']['base_unit']

        self.replace({'base_unit': 'seed_cluster'})

        packet = self.client.get(f"/seeds/packets/all/{self.packet['pk']}/")
        self.assertEqual(packet.data['inventory']['base_unit'], received_in)
        self.assertEqual(received_in, 'seed')

    def test_the_replacement_gets_an_inventory_item_of_its_own(self):
        """Its stock is counted in a different unit, so it cannot share one."""
        response = self.replace({'base_unit': 'seed_cluster'})

        source_item = response.data['source']['inventory_item']
        replacement_item = response.data['replacement']['inventory_item']
        self.assertIsNotNone(replacement_item)
        self.assertNotEqual(replacement_item, source_item)
        self.assertEqual(
            InventoryItem.objects.get(pk=replacement_item).base_unit,
            'seed_cluster',
        )

    def test_a_replaced_entry_is_no_longer_offered_as_a_choice(self):
        """Retirement is what takes it out of the selectors, and it applies."""
        response = self.replace({'base_unit': 'seed_cluster'})

        offerable = self.client.get('/seeds/seeds/', {'active': 'true'}).data

        self.assertEqual(
            {row['pk'] for row in offerable['results']},
            {response.data['replacement']['pk']},
        )

    def test_the_replaced_entry_is_still_readable(self):
        """It keeps the identity every posted packet was written against."""
        self.replace({'base_unit': 'seed_cluster'})

        detail = self.client.get(f'/seeds/seeds/{self.seeds_pk}/')

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['base_unit'], 'seed')


class SeedCatalogReplacementTrailTests(SeedCatalogTestCase):
    """A replacement leaves a trail that says where the catalog went."""

    variety_name = 'Beetroot'
    supplier_code = 'BEET-1'
    posted = True

    def test_a_replacement_that_changes_nothing_is_refused(self):
        """It would split the catalog in two and mean the same twice."""
        response = self.replace({'supplier_code': 'BEET-1'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['replacement'],
            ['Name what the replacement should say differently.'],
        )

    def test_an_entry_that_was_already_replaced_cannot_be_replaced_again(self):
        """The trail stays readable by saying where the catalog went."""
        self.replace({'base_unit': 'seed_cluster'})

        response = self.replace({'supplier_code': 'BEET-2'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('was already replaced by', response.data['replacement'][0])

    def test_a_refused_replacement_leaves_the_entry_where_it_was(self):
        """A replacement is the successor and the retirement, or neither."""
        self.replace({'base_unit': 'seed_cluster'})
        before = Seeds.objects.count()

        self.replace({'supplier_code': 'BEET-2'})

        self.assertEqual(Seeds.objects.count(), before)

    def test_a_replacement_may_correct_the_supplier_it_was_filed_under(self):
        """Any of the three can be what the entry got wrong."""
        other = make_supplier(name='Egmont Seeds')

        response = self.replace({'supplier': other.pk})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['replacement']['supplier'], other.pk)
        self.assertEqual(response.data['replacement']['base_unit'], 'seed')

    def test_a_retired_choice_cannot_be_replaced_onto(self):
        """A replacement is what gets offered, so it points at live records."""
        other = make_supplier(name='Egmont Seeds')
        self.client.patch(
            f'/supplies/supplier/{other.pk}/', {'active': False}, format='json',
        )

        response = self.replace({'supplier': other.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn('supplier', response.data)

    def test_the_successor_receives_the_packets_from_here_on(self):
        """The replacement is a working entry, not just a note in the trail."""
        replacement = self.replace({'base_unit': 'seed_cluster'}).data['replacement']

        packet = self.receive_packet(seeds_pk=replacement['pk'])

        self.assertEqual(packet['seeds'], replacement['pk'])
        self.assertEqual(packet['inventory']['base_unit'], 'seed_cluster')

    def test_a_second_replacement_extends_the_trail_one_hop_at_a_time(self):
        """Each entry is right about its own stock, so the chain is history."""
        first = self.replace({'base_unit': 'seed_cluster'}).data['replacement']
        self.receive_packet(seeds_pk=first['pk'])

        second = self.replace({'supplier_code': 'BEET-3'}, seeds_pk=first['pk'])

        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            second.data['source']['replaced_by'], second.data['replacement']['pk'],
        )
        self.assertEqual(
            Seeds.objects.get(pk=self.seeds_pk).replaced_by_id, first['pk'],
        )

    def test_a_seed_catalog_entry_still_has_no_merge_route(self):
        """Replacement is what a stock-owning record gets instead."""
        response = self.client.post(
            f'/seeds/seeds/{self.seeds_pk}/merge/',
            {'into': self.seeds_pk},
            format='json',
        )

        self.assertEqual(response.status_code, 404)


class TrayModelTestCase(RESTContractTestCase):
    """One tray model, created the way the Seed tray screen creates it."""

    identifier = '72-cell propagator'
    #: Whether the model starts with a physical tray received against it,
    #: which is what freezes the grid its cells were built to.
    received = False

    def setUp(self):
        super().setUp()
        self.store = make_location(name='Potting shed')
        self.model_pk = self.create_model()
        self.tray = self.receive_tray() if self.received else None

    def create_model(self, **overrides):
        """Create one tray model through the public workflow."""
        payload = {
            'identifier': self.identifier,
            'description': 'Rigid propagation tray',
            'height': 60,
            'x_size': 300,
            'y_size': 500,
            'x_cells': 8,
            'y_cells': 9,
            'cell_size_ml': 40,
        }
        payload.update(overrides)
        response = self.client.post(
            '/seedtrays/seedtraymodels/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['pk']

    def receive_tray(self, model_pk=None):
        """Receive one physical tray, which builds its cells from the grid."""
        response = self.client.post(
            f'/seedtrays/seedtraymodels/{model_pk or self.model_pk}/receive/',
            {
                'received_date': '2026-08-02',
                'quantity': 1,
                'line_cost_ex_tax': '12.5000',
                'destination': self.store.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['trays'][0]

    def correct(self, changes, model_pk=None):
        """Ask for an ordinary in-place edit of the model."""
        return self.client.patch(
            f'/seedtrays/seedtraymodels/{model_pk or self.model_pk}/',
            changes,
            format='json',
        )

    def preview(self, model_pk=None):
        """Ask what replacing the model would leave where it is."""
        return self.client.get(
            f'/seedtrays/seedtraymodels/{model_pk or self.model_pk}/replace/',
        )

    def replace(self, changes, model_pk=None):
        """Ask for the replacement itself."""
        return self.client.post(
            f'/seedtrays/seedtraymodels/{model_pk or self.model_pk}/replace/',
            changes,
            format='json',
        )


class TrayModelCorrectionTests(TrayModelTestCase):
    """A model with no trays against it is corrected in place."""

    def test_the_grid_of_an_unused_model_is_corrected_in_place(self):
        """Until a tray is built to it, a wrong grid is simply wrong."""
        response = self.correct({'x_cells': 6, 'y_cells': 12})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['x_cells'], 6)
        self.assertEqual(response.data['y_cells'], 12)
        self.assertIsNone(response.data['replaced_by'])

    def test_an_unused_model_reports_its_grid_as_editable(self):
        """The screen asks before it offers to correct or to replace."""
        response = self.preview()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['identity_locked'])
        self.assertEqual(response.data['identity_fields'], ['x_cells', 'y_cells'])
        self.assertEqual(response.data['blockers'], [])

    def test_a_rename_reaches_the_item_it_is_paired_with(self):
        """The catalog and the stock screens name the same tray."""
        self.correct({'identifier': '72-cell plug tray'})

        model = SeedTrayModel.objects.get(pk=self.model_pk)
        self.assertEqual(model.inventory_item.name, 'Tray model: 72-cell plug tray')

    def test_a_name_another_model_already_holds_is_refused(self):
        """A duplicate name used to reach the database as a 500."""
        other = self.create_model(identifier='104-cell propagator')

        response = self.correct({'identifier': self.identifier}, model_pk=other)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['identifier'],
            [f'Another tray model is already called {self.identifier}.'],
        )


class TrayModelReplacementTests(TrayModelTestCase):
    """A received tray freezes the grid its cells were built to."""

    received = True

    def test_a_received_tray_freezes_the_grid_it_was_built_to(self):
        """Editing it would say the tray on the shelf was always something else."""
        response = self.correct({'x_cells': 6, 'y_cells': 12})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['x_cells'],
            ['Replace the record instead of changing this after stock has been posted.'],
        )

    def test_the_measurements_stay_editable_after_a_tray_is_received(self):
        """A corrected measurement still describes the same tray."""
        response = self.correct({'height': 65, 'cell_size_ml': 45})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['height'], 65)
        self.assertEqual(response.data['cell_size_ml'], 45)

    def test_a_preview_names_the_trays_that_would_stay_where_they_are(self):
        """Nobody should have to replace a model to find out what is on it."""
        response = self.preview()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['identity_locked'])
        self.assertEqual(response.data['blockers'], [])
        relations = {entry['relation'] for entry in response.data['stays']}
        self.assertIn('seedtrays.seedtray.model', relations)

    def test_a_replacement_carries_the_corrected_grid(self):
        """The whole point is a model the next delivery is cut to."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        self.assertEqual(response.status_code, 200, response.data)
        replacement = response.data['replacement']
        self.assertEqual((replacement['x_cells'], replacement['y_cells']), (6, 12))
        self.assertTrue(replacement['active'])

    def test_a_replacement_inherits_everything_it_was_not_asked_to_change(self):
        """A correction names what is wrong, not the whole record again."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        replacement = response.data['replacement']
        self.assertEqual(replacement['height'], 60)
        self.assertEqual(replacement['x_size'], 300)
        self.assertEqual(replacement['cell_size_ml'], 40)
        self.assertEqual(replacement['description'], 'Rigid propagation tray')

    def test_the_replaced_model_is_retired_pointing_at_its_successor(self):
        """A reader who searches for the old model finds where it went."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        source = response.data['source']
        self.assertFalse(source['active'])
        self.assertEqual(source['replaced_by'], response.data['replacement']['pk'])

    def test_a_replacement_leaves_the_tray_on_the_model_it_was_built_to(self):
        """Its cells are at the coordinates the old grid gave them."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        tray = self.client.get(f"/seedtrays/seedtrays/{self.tray['pk']}/")
        self.assertEqual(tray.data['model'], self.model_pk)
        stayed = {entry['relation'] for entry in response.data['stayed']}
        self.assertIn('seedtrays.seedtray.model', stayed)

    def test_the_replacement_gets_a_tray_item_of_its_own(self):
        """A tray of the corrected grid is a different unit of a different item."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        source_item = response.data['source']['inventory_item']
        replacement_item = response.data['replacement']['inventory_item']
        self.assertNotEqual(replacement_item, source_item)
        item = InventoryItem.objects.get(pk=replacement_item)
        self.assertEqual(item.name, 'Tray model: 72-cell (6x12)')
        self.assertEqual(item.category, InventoryItem.Category.TRAY)
        self.assertEqual(item.tracking_mode, InventoryItem.TrackingMode.SERIALIZED)

    def test_a_replacement_must_be_given_a_name_of_its_own(self):
        """The model it supersedes keeps the name its trays were received under."""
        response = self.replace({'x_cells': 6, 'y_cells': 12})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['identifier'],
            [f'Another tray model is already called {self.identifier}.'],
        )
        self.assertEqual(SeedTrayModel.objects.count(), 1)

    def test_a_replaced_model_is_no_longer_offered_as_a_choice(self):
        """Retirement is what takes it out of the selectors, and it applies."""
        response = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        )

        offerable = self.client.get(
            '/seedtrays/seedtraymodels/', {'active': 'true'},
        ).data

        self.assertEqual(
            {row['pk'] for row in offerable['results']},
            {response.data['replacement']['pk']},
        )

    def test_the_successor_receives_the_next_delivery(self):
        """The replacement is a working model, not just a note in the trail."""
        replacement = self.replace(
            {'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12},
        ).data['replacement']

        tray = self.receive_tray(model_pk=replacement['pk'])

        self.assertEqual(tray['model'], replacement['pk'])
        cells = self.client.get(f"/seedtrays/seedtrays/{tray['pk']}/cells/")
        self.assertEqual(len(cells.data), 72)
        self.assertEqual(max(cell['x_position'] for cell in cells.data), 5)

    def test_a_model_that_was_already_replaced_cannot_be_replaced_again(self):
        """The trail stays readable by saying where the catalog went."""
        self.replace({'identifier': '72-cell (6x12)', 'x_cells': 6, 'y_cells': 12})

        response = self.replace({'identifier': '72-cell (4x18)', 'x_cells': 4, 'y_cells': 18})

        self.assertEqual(response.status_code, 400)
        self.assertIn('was already replaced by', response.data['replacement'][0])

    def test_a_tray_model_still_has_no_merge_route(self):
        """Replacement is what a stock-owning record gets instead."""
        response = self.client.post(
            f'/seedtrays/seedtraymodels/{self.model_pk}/merge/',
            {'into': self.model_pk},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
