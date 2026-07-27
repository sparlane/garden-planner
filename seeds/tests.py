"""
Tests related to seeds
"""
from plantings.models import (
    GardenSquareDirectSowPlanting,
    GardenSquareTransplant,
    SeedTrayPlanting,
)
from tests.api import RESTContractTestCase
from tests.factories import (
    make_garden_square,
    make_seed_packet,
    make_seed_tray,
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
        """Seed products and packets survive create and retrieve."""
        seeds = self.assert_create_retrieve(
            '/seeds/seeds/',
            {
                'supplier': self.packet.seeds.supplier_id,
                'plant_variety': self.packet.seeds.plant_variety_id,
                'supplier_code': 'CARROT-01',
                'url': 'https://seeds.example.com/carrot',
                'notes': 'Pelleted seed',
            },
        )
        self.assert_create_retrieve(
            '/seeds/packets/',
            {
                'seeds': seeds['pk'],
                'purchase_date': '2026-03-01',
                'sow_by': '2028-03-01',
                'empty': False,
                'notes': 'Opened packet',
            },
        )

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

        first_tray_planting = SeedTrayPlanting.objects.create(
            seeds_used=used_packet,
            quantity=4,
            seed_tray=tray,
        )
        second_tray_planting = SeedTrayPlanting.objects.create(
            seeds_used=used_packet,
            quantity=6,
            seed_tray=tray,
        )
        for quantity in (3, 5):
            GardenSquareDirectSowPlanting.objects.create(
                seeds_used=used_packet,
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
