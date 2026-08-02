"""Integration tests for audited seed consumption and correction."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from inventory.models import QuantityCertainty, StockMovement
from seeds.models import SeedPacket
from seeds.services import packet_inventory_snapshot
from tests.factories import (
    make_garden_row,
    make_garden_square,
    make_plant_variety,
    make_seed_tray,
    make_seed_tray_cell,
    make_supplier,
)

from .models import (
    GardenSquareDirectSowPlanting,
    SeedTrayPlanting,
    SowingStockPosting,
)


class SowingInventoryTests(APITestCase):
    """Every new sowing consumes its selected physical packet atomically."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='sowing-stock')
        self.client.force_authenticate(self.user)
        self.row = make_garden_row()
        self.square = make_garden_square()
        self.tray = make_seed_tray()
        self.cell = make_seed_tray_cell(tray=self.tray)
        self.packet = self.receive_packet(QuantityCertainty.EXACT, '20')

    def receive_packet(self, certainty, quantity=None):
        """Receive one packet through the same API used by the browser."""
        supplier = make_supplier()
        variety = make_plant_variety()
        catalog = self.client.post(
            '/seeds/seeds/',
            {
                'supplier': supplier.pk,
                'plant_variety': variety.pk,
                'base_unit': 'seed_cluster',
            },
            format='json',
        )
        self.assertEqual(catalog.status_code, 201)
        payload = {
            'seeds': catalog.data['pk'],
            'quantity_certainty': certainty,
            'line_price': '5.0000',
            'received_date': '2026-08-02',
        }
        if quantity is not None:
            payload['quantity'] = quantity
        draft = self.client.post(
            '/seeds/packet-receipts/',
            payload,
            format='json',
        )
        self.assertEqual(draft.status_code, 201)
        packet = self.client.post(
            f"/seeds/packet-receipts/{draft.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(packet.status_code, 201)
        return SeedPacket.objects.get(pk=packet.data['pk'])

    def test_each_sowing_type_posts_packet_consumption(self):
        """Row, square, and tray creates share the same stock contract."""
        requests = (
            (
                '/plantings/directsowgardenrow/',
                {'quantity': 2, 'location': self.row.pk},
            ),
            (
                '/plantings/directsowgardensquare/',
                {'quantity': 3, 'location': self.square.pk},
            ),
            (
                '/plantings/seedtray/',
                {
                    'quantity': 4,
                    'seed_tray': self.tray.pk,
                    'cell_plantings': [{'cell': self.cell.pk, 'quantity': 4}],
                },
            ),
        )
        for url, payload in requests:
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {'seeds_used': self.packet.pk, **payload},
                    format='json',
                )
                self.assertEqual(response.status_code, 201)

        self.packet.refresh_from_db()
        snapshot = packet_inventory_snapshot(self.packet)
        self.assertEqual(snapshot['sown_quantity'], Decimal('9'))
        self.assertEqual(snapshot['remaining_quantity'], Decimal('11'))
        self.assertEqual(SowingStockPosting.objects.count(), 3)

    def test_insufficient_known_stock_rolls_back_planting(self):
        """The planting never survives a rejected exact-packet consumption."""
        before_movements = StockMovement.objects.count()
        response = self.client.post(
            '/plantings/directsowgardensquare/',
            {
                'seeds_used': self.packet.pk,
                'quantity': 21,
                'location': self.square.pk,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GardenSquareDirectSowPlanting.objects.exists())
        self.assertEqual(StockMovement.objects.count(), before_movements)

    def test_unknown_packet_records_use_without_claiming_remaining_stock(self):
        """Unknown contents accept sowings and retain a nullable balance."""
        packet = self.receive_packet(QuantityCertainty.UNKNOWN)
        response = self.client.post(
            '/plantings/directsowgardensquare/',
            {
                'seeds_used': packet.pk,
                'quantity': 7,
                'location': self.square.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        snapshot = packet_inventory_snapshot(packet)
        self.assertEqual(snapshot['sown_quantity'], Decimal('7'))
        self.assertIsNone(snapshot['remaining_quantity'])

        count = self.client.post(
            f'/seeds/packets/{packet.pk}/reconcile/',
            {
                'counted_quantity': '12',
                'quantity_certainty': 'exact',
                'reason': 'Counted after sowing',
            },
            format='json',
        )
        self.assertEqual(count.status_code, 200)
        self.assertEqual(
            count.data['inventory']['received_quantity'],
            '19.000000000',
        )
        self.assertEqual(
            count.data['inventory']['remaining_quantity'],
            '12.000000000',
        )

    def test_correction_reverses_and_reposts_without_silent_edits(self):
        """Quantity mistakes retain the original movement and lock generic writes."""
        created = self.client.post(
            '/plantings/directsowgardensquare/',
            {
                'seeds_used': self.packet.pk,
                'quantity': 4,
                'location': self.square.pk,
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        url = f"/plantings/directsowgardensquare/{created.data['pk']}/"
        corrected = self.client.post(
            f'{url}correct-sowing/',
            {'quantity': 6, 'reason': 'Two clusters were omitted'},
            format='json',
        )

        self.assertEqual(corrected.status_code, 200)
        movement_ids = {
            corrected.data['original_movement'],
            corrected.data['reversal_movement'],
            corrected.data['replacement_movement'],
        }
        self.assertEqual(len(movement_ids), 3)
        self.assertEqual(corrected.data['planting']['quantity'], 6)
        self.assertEqual(
            packet_inventory_snapshot(self.packet)['remaining_quantity'],
            Decimal('14'),
        )

        patch = self.client.patch(url, {'quantity': 7}, format='json')
        delete = self.client.delete(url)
        self.assertEqual(patch.status_code, 400)
        self.assertEqual(delete.status_code, 400)

        removed = self.client.patch(url, {'removed': True}, format='json')
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(
            packet_inventory_snapshot(self.packet)['remaining_quantity'],
            Decimal('14'),
        )

    def test_tray_correction_cannot_undercut_cell_allocations(self):
        """Stock correction preserves the tray allocation invariant."""
        created = self.client.post(
            '/plantings/seedtray/',
            {
                'seeds_used': self.packet.pk,
                'quantity': 5,
                'seed_tray': self.tray.pk,
                'cell_plantings': [{'cell': self.cell.pk, 'quantity': 5}],
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201)
        response = self.client.post(
            f"/plantings/seedtray/{created.data['pk']}/correct-sowing/",
            {'quantity': 4, 'reason': 'Mistaken total'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        planting = SeedTrayPlanting.objects.get(pk=created.data['pk'])
        self.assertEqual(planting.quantity, 5)
