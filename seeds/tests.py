"""
Tests related to seeds
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from inventory.models import QuantityCertainty, StockMovement, StockReceipt
from seeds.models import SeedPacketQuantityReconciliation
from seeds.services import packet_inventory_snapshot, reverse_packet_reconciliation
from plantings.models import (
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    SeedTrayCellPlanting,
    SeedTrayPlanting,
    SpecificPlant,
    SpecificPlantLocation,
)
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_garden_square,
    make_plant_variety,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_supplier,
)


class SeedRESTContractTests(RESTContractTestCase):
    """Smoke tests for seed-product and packet REST resources."""

    LIST_URLS = (
        '/seeds/seeds/',
        '/seeds/packets/',
        '/seeds/packets/all/',
    )

    def setUp(self):
        super().setUp()
        self.packet = make_seed_packet()

    def test_list_routes_require_authentication(self):
        """Anonymous requests cannot list seed resources."""
        self.assert_authentication_required(self.LIST_URLS)

    def test_list_routes_return_lists(self):
        """Authenticated seed collections use the common list contract."""
        self.assert_list_contract(self.LIST_URLS)

    def test_resources_round_trip(self):
        """Seed products create inventory identities and packets require receipts."""
        seeds = self.assert_create_retrieve(
            '/seeds/seeds/',
            {
                'supplier': self.packet.seeds.supplier_id,
                'plant_variety': self.packet.seeds.plant_variety_id,
                'supplier_code': 'CARROT-01',
                'url': 'https://seeds.example.com/carrot',
                'notes': 'Pelleted seed',
                'base_unit': 'seed_cluster',
            },
        )
        self.assertEqual(seeds['base_unit'], 'seed_cluster')
        response = self.client.post(
            '/seeds/packets/',
            {
                'seeds': seeds['pk'],
                'purchase_date': '2026-03-01',
                'sow_by': '2028-03-01',
                'empty': False,
                'notes': 'Opened packet',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 405)

    def test_current_and_all_packet_routes_apply_empty_filter(self):
        """The current route omits empty packets while the all route retains them."""
        empty_packet = make_seed_packet(empty=True)

        current_response = self.client.get('/seeds/packets/')
        all_response = self.client.get('/seeds/packets/all/')

        self.assertEqual(current_response.status_code, 200)
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(
            {packet['pk'] for packet in current_response.data},
            {self.packet.pk},
        )
        self.assertEqual(
            {packet['pk'] for packet in all_response.data},
            {self.packet.pk, empty_packet.pk},
        )

    def test_current_packet_summary_coalesces_usage_without_fan_out(self):
        """Packet summaries total each planting path independently."""
        used_packet = make_seed_packet(notes='Partially used packet')
        empty_packet = make_seed_packet(empty=True, notes='Fully used packet')
        tray = make_seed_tray()
        square = make_garden_square()

        batch = make_batch_for_packet(used_packet)
        first_tray_planting = SeedTrayPlanting.objects.create(
            seeds_used=used_packet,
            batch=batch,
            quantity=4,
            seed_tray=tray,
        )
        second_tray_planting = SeedTrayPlanting.objects.create(
            seeds_used=used_packet,
            batch=batch,
            quantity=6,
            seed_tray=tray,
        )
        for quantity in (3, 5):
            GardenSquareDirectSowPlanting.objects.create(
                seeds_used=used_packet,
                batch=batch,
                quantity=quantity,
                location=square,
            )
        GardenSquareTransplant.objects.create(
            original_planting=first_tray_planting,
            quantity=1,
            location=square,
        )
        GardenSquareTransplant.objects.create(
            original_planting=second_tray_planting,
            quantity=2,
            location=square,
        )

        self.client.force_login(self.user)
        response = self.client.get('/seeds/packets/current/')

        self.assertEqual(response.status_code, 200)
        packets = {
            packet['pk']: packet
            for packet in response.json()['packets']
        }
        self.assertNotIn(empty_packet.pk, packets)
        self.assertEqual(
            {
                key: packets[self.packet.pk][key]
                for key in (
                    'seeds_planted_trays',
                    'seeds_planted_direct',
                    'transplanted_count',
                )
            },
            {
                'seeds_planted_trays': 0,
                'seeds_planted_direct': 0,
                'transplanted_count': 0,
            },
        )
        self.assertEqual(
            {
                key: packets[used_packet.pk][key]
                for key in (
                    'seeds_planted_trays',
                    'seeds_planted_direct',
                    'transplanted_count',
                )
            },
            {
                'seeds_planted_trays': 10,
                'seeds_planted_direct': 8,
                'transplanted_count': 3,
            },
        )

    def test_current_packet_summary_separates_multigerm_plants_from_seeds(self):
        """Two sown clusters can yield five transplanted individual plants."""
        packet = make_seed_packet(notes='Multigerm packet')
        tray = make_seed_tray()
        cell = make_seed_tray_cell(tray=tray)
        square = make_garden_square()
        planting = SeedTrayPlanting.objects.create(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
            quantity=2,
            seed_tray=tray,
        )
        cell_planting = SeedTrayCellPlanting.objects.create(
            seed_tray_planting=planting,
            cell=cell,
            quantity=2,
        )
        plants = SpecificPlant.objects.bulk_create([
            SpecificPlant(cell_planting=cell_planting)
            for _index in range(5)
        ])
        SpecificPlantLocation.objects.bulk_create([
            SpecificPlantLocation(
                specific_plant=plant,
                location_type=SpecificPlantLocation.GARDEN_SQUARE,
                garden_square=square,
            )
            for plant in plants
        ])
        legacy = GardenSquareTransplant.objects.create(
            original_planting=planting,
            quantity=5,
            location=square,
        )
        self.client.force_login(self.user)

        for delete_legacy in (False, True):
            with self.subTest(legacy_present=not delete_legacy):
                if delete_legacy:
                    legacy.delete()
                response = self.client.get('/seeds/packets/current/')
                summary = next(
                    item for item in response.json()['packets']
                    if item['pk'] == packet.pk
                )
                self.assertEqual(summary['seeds_planted_trays'], 2)
                self.assertEqual(summary['transplanted_count'], 5)


class SeedPacketInventoryWorkflowTests(APITestCase):
    """Seed receipt drafts preserve exact, estimated, and unknown stock truth."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='packet-stock')
        self.client.force_authenticate(self.user)
        supplier = make_supplier()
        variety = make_plant_variety()
        response = self.client.post(
            '/seeds/seeds/',
            {
                'supplier': supplier.pk,
                'plant_variety': variety.pk,
                'supplier_code': 'BEET-CLUSTER',
                'base_unit': 'seed_cluster',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.seeds_pk = response.data['pk']

    def create_draft(self, certainty, quantity=None, price='6.0000'):
        """Create one packet receipt through the public seed workflow."""
        payload = {
            'seeds': self.seeds_pk,
            'quantity_certainty': certainty,
            'line_price': price,
            'received_date': '2026-08-02',
            'sow_by': '2028-08-02',
            'supplier_lot_reference': 'SUPPLIER-LOT-1',
            'notes': 'Silverbeet clusters',
        }
        if quantity is not None:
            payload['quantity'] = quantity
        response = self.client.post(
            '/seeds/packet-receipts/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        return response.data

    def post_draft(self, draft_pk):
        """Confirm a receipt draft and return the packet response."""
        response = self.client.post(
            f'/seeds/packet-receipts/{draft_pk}/post/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        return response.data

    def test_unknown_receipt_posts_lot_without_inventing_quantity(self):
        """A priced packet can be received while its contents remain unknown."""
        draft = self.create_draft(QuantityCertainty.UNKNOWN)
        packet = self.post_draft(draft['pk'])

        self.assertEqual(packet['inventory']['quantity_certainty'], 'unknown')
        self.assertIsNone(packet['inventory']['received_quantity'])
        self.assertIsNone(packet['inventory']['remaining_quantity'])
        self.assertIsNone(packet['empty'])
        receipt = StockReceipt.objects.get(seed_packet_draft__pk=draft['pk'])
        self.assertEqual(receipt.status, StockReceipt.Status.POSTED)
        self.assertFalse(
            StockMovement.objects.filter(receipt_line__receipt=receipt).exists(),
        )

    def test_unknown_packet_count_establishes_balance_and_effective_cost(self):
        """A later count fills the missing inbound quantity without rewriting receipt."""
        packet = self.post_draft(
            self.create_draft(QuantityCertainty.UNKNOWN)['pk'],
        )
        response = self.client.post(
            f"/seeds/packets/{packet['pk']}/reconcile/",
            {
                'counted_quantity': '24',
                'quantity_certainty': QuantityCertainty.EXACT,
                'reason': 'Counted packet contents',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        inventory = response.data['inventory']
        self.assertEqual(inventory['quantity_certainty'], 'exact')
        self.assertEqual(inventory['received_quantity'], '24.000000000')
        self.assertEqual(inventory['remaining_quantity'], '24.000000000')
        self.assertEqual(
            inventory['effective_base_unit_cost'],
            '0.250000000000',
        )
        self.assertFalse(response.data['empty'])

    def test_reversing_first_count_restores_unknown_packet_certainty(self):
        """A stocktake reversal does not leave invented packet certainty behind."""
        packet = self.post_draft(
            self.create_draft(QuantityCertainty.UNKNOWN)['pk'],
        )
        self.client.post(
            f"/seeds/packets/{packet['pk']}/reconcile/",
            {
                'counted_quantity': '24',
                'quantity_certainty': QuantityCertainty.EXACT,
                'reason': 'Counted packet contents',
            },
            format='json',
        )
        reconciliation = SeedPacketQuantityReconciliation.objects.get(
            packet_id=packet['pk'], reversal_of__isnull=True,
        )

        reversal = reverse_packet_reconciliation(
            reconciliation, self.user, 'Counted the wrong packet.',
        )
        snapshot = packet_inventory_snapshot(reversal.packet)

        self.assertEqual(snapshot['quantity_certainty'], QuantityCertainty.UNKNOWN)
        self.assertIsNone(snapshot['remaining_quantity'])
        self.assertEqual(reversal.reversal_of, reconciliation)

    def test_estimated_packet_count_posts_audited_loss(self):
        """A low physical count corrects the balance instead of editing receipt history."""
        packet = self.post_draft(
            self.create_draft(QuantityCertainty.ESTIMATED, '30')['pk'],
        )
        response = self.client.post(
            f"/seeds/packets/{packet['pk']}/reconcile/",
            {
                'counted_quantity': '27',
                'quantity_certainty': QuantityCertainty.EXACT,
                'reason': 'Counted three fewer clusters',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['inventory']['remaining_quantity'],
            '27.000000000',
        )
        self.assertTrue(StockMovement.objects.filter(
            lot_id=packet['inventory']['lot'],
            movement_type=StockMovement.MovementType.ADJUSTMENT_LOSS,
            quantity=Decimal('3'),
        ).exists())

    def test_draft_can_be_cancelled_but_posted_packet_cannot(self):
        """Draft cleanup removes its reserved container without erasing history."""
        draft = self.create_draft(QuantityCertainty.EXACT, '10')
        response = self.client.delete(
            f"/seeds/packet-receipts/{draft['pk']}/",
        )
        self.assertEqual(response.status_code, 204)

        posted = self.create_draft(QuantityCertainty.EXACT, '10')
        self.post_draft(posted['pk'])
        response = self.client.delete(
            f"/seeds/packet-receipts/{posted['pk']}/",
        )
        self.assertEqual(response.status_code, 400)

    def test_seed_drafts_are_flagged_and_refused_by_the_general_receipt_api(self):
        """The general receiving screen can neither see nor post a seed draft."""
        draft = self.create_draft(QuantityCertainty.EXACT, '10')
        receipt = StockReceipt.objects.get(seed_packet_draft__pk=draft['pk'])
        detail = f'/inventory/receipts/{receipt.pk}/'

        hidden = self.client.get(
            '/inventory/receipts/',
            {'seed_packet': 'false'},
        )
        self.assertNotIn(receipt.pk, [entry['pk'] for entry in hidden.data])
        listed = self.client.get('/inventory/receipts/', {'seed_packet': 'true'})
        self.assertEqual([entry['pk'] for entry in listed.data], [receipt.pk])
        self.assertIs(listed.data[0]['is_seed_packet_draft'], True)

        self.assertEqual(
            self.client.patch(detail, {'notes': 'General edit'}, format='json').status_code,
            400,
        )
        self.assertEqual(self.client.delete(detail).status_code, 400)
        self.assertEqual(
            self.client.post(f'{detail}post/', {}, format='json').status_code,
            400,
        )
