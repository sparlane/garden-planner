"""REST contract tests for inventory ledger resources and domain actions."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from supplies.models import Supplier
from workspaces.models import Workspace, get_current_workspace

from .models import (
    InventoryItem,
    InventoryLocation,
    StockLot,
    StockMovement,
    StockReceipt,
    Stocktake,
)
from .units import UnitCode


class LedgerRestTests(APITestCase):
    """Ledger APIs are scoped, explicit, normalized, and append-only."""

    location_url = '/inventory/locations/'
    receipt_url = '/inventory/receipts/'
    lot_url = '/inventory/lots/'
    balance_url = '/inventory/balances/'
    movement_url = '/inventory/movements/'
    stocktake_url = '/inventory/stocktakes/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        Workspace.objects.filter(pk=self.workspace.pk).update(
            currency_code='NZD',
            default_tax_rate=Decimal('15'),
        )
        self.workspace.refresh_from_db()
        self.user = get_user_model().objects.create_user(username='ledger-api-user')
        self.client.force_authenticate(self.user)
        self.supplier = Supplier.objects.create(
            workspace=self.workspace,
            name='API Supplier',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='API growing media',
            sku='MEDIA-API-LEDGER',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
            reorder_level=Decimal('250'),
        )
        self.store = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='API store',
            code='API-STORE',
            location_type=InventoryLocation.LocationType.STORAGE,
        )
        self.growing = InventoryLocation.objects.create(
            workspace=self.workspace,
            name='API growing house',
            code='API-GROW',
            location_type=InventoryLocation.LocationType.GROWING,
        )

    def receipt_payload(self, **overrides):
        """Return a valid nested draft receipt request."""
        payload = {
            'supplier': self.supplier.pk,
            'received_date': '2026-08-01',
            'supplier_reference': 'INV-API-1',
            'tax_recoverable': False,
            'lines': [
                {
                    'item': self.item.pk,
                    'supplier_lot_reference': 'SUP-LOT-1',
                    'expires_on': '2027-08-01',
                    'quantity': '2.000000000',
                    'unit_code': UnitCode.LITRE,
                    'line_cost_ex_tax': '10.0000',
                    'destination': self.store.pk,
                },
            ],
        }
        payload.update(overrides)
        return payload

    def create_and_post_receipt(self):
        """Create and post one receipt, returning response records."""
        created = self.client.post(
            self.receipt_url,
            self.receipt_payload(),
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        posted = self.client.post(
            f"{self.receipt_url}{created.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        lot = StockLot.objects.get(receipt_line__receipt_id=created.data['pk'])
        return created.data, posted.data, lot

    def opening_payload(self, **overrides):
        """Return a valid opening-balance request."""
        payload = {
            'item': self.item.pk,
            'quantity': '500.000000000',
            'unit_code': UnitCode.MILLILITRE,
            'destination': self.store.pk,
            'acquisition_total': '25.0000',
            'received_on': '2026-08-01',
            'reason': 'Audited opening',
        }
        payload.update(overrides)
        return payload

    def create_opening(self, **overrides):
        """Post and return one opening lot response."""
        response = self.client.post(
            f'{self.movement_url}opening/',
            self.opening_payload(**overrides),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_authentication_is_required_for_every_collection(self):
        """No ledger or balance data is anonymously readable."""
        self.client.force_authenticate(user=None)
        for url in (
            self.location_url,
            self.receipt_url,
            self.lot_url,
            self.balance_url,
            self.movement_url,
            self.stocktake_url,
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_locations_are_filterable_and_used_locations_cannot_be_deleted(self):
        """Unused places may be removed while ledger places require deactivation."""
        created = self.client.post(
            self.location_url,
            {
                'name': 'Temporary receiving',
                'code': 'TEMP-RECV',
                'location_type': InventoryLocation.LocationType.RECEIVING,
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(
            self.client.delete(
                f"{self.location_url}{created.data['pk']}/",
            ).status_code,
            204,
        )

        opening = self.create_opening()
        response = self.client.delete(f'{self.location_url}{self.store.pk}/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.data)
        self.assertEqual(opening['movement']['destination'], self.store.pk)

        filtered = self.client.get(
            self.location_url,
            {'active': 'true', 'location_type': 'growing'},
        )
        self.assertEqual(
            [location['pk'] for location in filtered.data],
            [self.growing.pk],
        )

    def test_nested_receipt_normalizes_posts_and_reverses(self):
        """Receipt actions preserve display data, provenance, and inverse history."""
        created, posted, lot = self.create_and_post_receipt()
        self.assertEqual(created['status'], StockReceipt.Status.DRAFT)
        self.assertEqual(created['currency_code'], 'NZD')
        self.assertEqual(created['tax_rate'], '15.0000')
        self.assertEqual(created['lines'][0]['base_quantity'], '2000.000000000')
        self.assertIsNone(created['lines'][0]['lot'])

        self.assertEqual(posted['status'], StockReceipt.Status.POSTED)
        self.assertEqual(posted['lines'][0]['lot'], lot.pk)
        self.assertEqual(lot.acquisition_total, Decimal('11.5000'))
        self.assertEqual(len(posted['movement_ids']), 1)
        self.assertEqual(
            self.client.patch(
                f"{self.receipt_url}{created['pk']}/",
                {'notes': 'Silent edit'},
                format='json',
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.delete(
                f"{self.receipt_url}{created['pk']}/",
            ).status_code,
            400,
        )

        reversed_response = self.client.post(
            f"{self.receipt_url}{created['pk']}/reverse/",
            {'reason': 'Supplier cancelled delivery'},
            format='json',
        )
        self.assertEqual(reversed_response.status_code, 200, reversed_response.data)
        self.assertEqual(reversed_response.data['status'], StockReceipt.Status.REVERSED)
        self.assertEqual(
            StockMovement.objects.filter(lot=lot).count(),
            2,
        )

    def test_draft_lines_can_be_replaced_and_invalid_selectors_are_rejected(self):
        """PATCH replaces draft lines without permitting foreign workspace IDs."""
        created = self.client.post(
            self.receipt_url,
            self.receipt_payload(),
            format='json',
        )
        replacement = self.receipt_payload()['lines'][0]
        replacement['quantity'] = '1.000000000'
        patched = self.client.patch(
            f"{self.receipt_url}{created.data['pk']}/",
            {'lines': [replacement]},
            format='json',
        )
        self.assertEqual(patched.status_code, 200, patched.data)
        self.assertEqual(len(patched.data['lines']), 1)
        self.assertEqual(patched.data['lines'][0]['base_quantity'], '1000.000000000')

        other = Workspace.objects.create(name='Foreign ledger API workspace')
        foreign_item = InventoryItem.objects.create(
            workspace=other,
            name='Foreign API item',
            category=InventoryItem.Category.PACKAGING,
            base_unit=UnitCode.EACH,
        )
        payload = self.receipt_payload()
        payload['lines'][0]['item'] = foreign_item.pk
        rejected = self.client.post(self.receipt_url, payload, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('item', rejected.data['lines'][0])

    def test_typed_actions_post_every_standalone_movement_shape(self):
        """Explicit endpoints append transfers, outflows, returns, and adjustments."""
        opening = self.create_opening()
        lot = opening['lot']['pk']
        common = {
            'lot': lot,
            'quantity': '25.000000000',
            'unit_code': UnitCode.MILLILITRE,
        }
        actions = [
            (
                'transfer',
                {**common, 'source': self.store.pk, 'destination': self.growing.pk},
                StockMovement.MovementType.TRANSFER,
            ),
            (
                'consume',
                {**common, 'source': self.growing.pk},
                StockMovement.MovementType.CONSUMPTION,
            ),
            (
                'adjust',
                {
                    **common,
                    'direction': 'gain',
                    'location': self.store.pk,
                    'reason': 'Found sealed container',
                },
                StockMovement.MovementType.ADJUSTMENT_GAIN,
            ),
            (
                'waste',
                {**common, 'source': self.store.pk, 'reason': 'Spilled'},
                StockMovement.MovementType.WASTE,
            ),
            (
                'sale',
                {**common, 'source': self.store.pk, 'reference': 'SALE-1'},
                StockMovement.MovementType.SALE,
            ),
            (
                'customer-return',
                {**common, 'destination': self.store.pk, 'reference': 'RETURN-1'},
                StockMovement.MovementType.CUSTOMER_RETURN,
            ),
        ]
        movement_ids = []
        for action_name, payload, expected_type in actions:
            with self.subTest(action=action_name):
                response = self.client.post(
                    f'{self.movement_url}{action_name}/',
                    payload,
                    format='json',
                )
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['movement_type'], expected_type)
                self.assertEqual(response.data['quantity'], '25.000000000')
                movement_ids.append(response.data['pk'])

        reversed_response = self.client.post(
            f'{self.movement_url}{movement_ids[1]}/reverse/',
            {'reason': 'Wrong batch'},
            format='json',
        )
        self.assertEqual(reversed_response.status_code, 201, reversed_response.data)
        self.assertEqual(
            reversed_response.data['movement_type'],
            StockMovement.MovementType.REVERSAL,
        )
        self.assertEqual(reversed_response.data['reversal_of'], movement_ids[1])

    def test_negative_balance_and_unexplained_waste_are_rejected(self):
        """Typed actions expose field errors without writing partial movements."""
        opening = self.create_opening(quantity='10.000000000')
        payload = {
            'lot': opening['lot']['pk'],
            'quantity': '11.000000000',
            'unit_code': UnitCode.MILLILITRE,
            'source': self.store.pk,
        }
        rejected = self.client.post(
            f'{self.movement_url}consume/',
            payload,
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('quantity', rejected.data)

        payload['quantity'] = '1.000000000'
        rejected = self.client.post(
            f'{self.movement_url}waste/',
            payload,
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('reason', rejected.data)

    def test_balances_return_valuation_low_stock_and_expiry_filters(self):
        """Derived rows expose current physical/available stock and immutable cost."""
        near_expiry = date.today() + timedelta(days=5)
        opening = self.create_opening(
            quantity='200.000000000',
            acquisition_total='10.0000',
            expires_on=near_expiry.isoformat(),
        )
        response = self.client.get(
            self.balance_url,
            {
                'low_stock': 'true',
                'expires_before': (near_expiry + timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row['lot'], opening['lot']['pk'])
        self.assertEqual(row['physical_quantity'], '200.000000000')
        self.assertEqual(row['available_quantity'], '200.000000000')
        self.assertEqual(row['reserved_quantity'], '0.000000000')
        self.assertEqual(row['valuation'], '10.0000')
        self.assertTrue(row['low_stock'])

        self.create_opening(quantity='100.000000000')
        response = self.client.get(self.balance_url, {'low_stock': 'true'})
        self.assertEqual(response.data, [])

    def test_stocktake_workflow_posts_and_reverses_variance(self):
        """Count documents expose expected, variance, and linked movement IDs."""
        opening = self.create_opening(quantity='100.000000000')
        created = self.client.post(
            self.stocktake_url,
            {
                'counted_at': '2026-08-02T10:00:00Z',
                'notes': 'API stocktake',
                'lines': [
                    {
                        'lot': opening['lot']['pk'],
                        'location': self.store.pk,
                        'counted_quantity': '90.000000000',
                        'unit_code': UnitCode.MILLILITRE,
                        'reason': 'Spill found during count',
                    },
                ],
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        posted = self.client.post(
            f"{self.stocktake_url}{created.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(posted.data['status'], Stocktake.Status.POSTED)
        self.assertEqual(
            posted.data['lines'][0]['expected_base_quantity'],
            '100.000000000',
        )
        self.assertEqual(
            posted.data['lines'][0]['variance_base_quantity'],
            '-10.000000000',
        )
        self.assertEqual(len(posted.data['lines'][0]['movement_ids']), 1)

        reversed_response = self.client.post(
            f"{self.stocktake_url}{created.data['pk']}/reverse/",
            {'reason': 'Counted the wrong shelf'},
            format='json',
        )
        self.assertEqual(reversed_response.status_code, 200, reversed_response.data)
        self.assertEqual(reversed_response.data['status'], Stocktake.Status.REVERSED)

    def test_lots_and_movements_are_read_only_and_filterable(self):
        """History resources reject generic writes and retain exact filters."""
        opening = self.create_opening()
        lot_pk = opening['lot']['pk']
        movement_pk = opening['movement']['pk']
        lots = self.client.get(
            self.lot_url,
            {'item': self.item.pk, 'identifier': 'LOT-'},
        )
        self.assertEqual([lot['pk'] for lot in lots.data], [lot_pk])
        movements = self.client.get(
            self.movement_url,
            {
                'lot': lot_pk,
                'location': self.store.pk,
                'movement_type': StockMovement.MovementType.OPENING,
            },
        )
        self.assertEqual(
            [movement['pk'] for movement in movements.data],
            [movement_pk],
        )
        self.assertEqual(
            self.client.patch(
                f'{self.lot_url}{lot_pk}/',
                {'identifier': 'EDITED'},
                format='json',
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.delete(
                f'{self.movement_url}{movement_pk}/',
            ).status_code,
            405,
        )
