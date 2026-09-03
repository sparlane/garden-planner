"""Receiving supplier documents that mix bulk stock and seed tray assets."""

# pylint: disable=duplicate-code

from decimal import Decimal

from seedtrays.models import SeedTray, SeedTrayCell, SeedTrayModel

from .models import InventoryItem, InventoryUnit, StockLot, StockMovement
from .test_ledger_rest import LedgerRestFixture
from .units import UnitCode


class MixedReceiptTests(LedgerRestFixture):
    """General receipts accept mapped trays without weakening their identity."""

    def line_payload(self, **overrides):
        """Return the complete line shape sent by the receiving editor."""
        line = self.receipt_payload()['lines'][0]
        line.update(overrides)
        return line

    def tray_model(self, **overrides):
        """Create a mapped serialized tray catalog item."""
        values = {
            'workspace': self.workspace,
            'identifier': 'API-40',
            'height': 50,
            'x_size': 300,
            'y_size': 200,
            'x_cells': 8,
            'y_cells': 5,
            'cell_size_ml': 45,
        }
        values.update(overrides)
        return SeedTrayModel.objects.create(**values)

    def test_mixed_receipt_posts_materials_and_mapped_seed_trays(self):
        """One supplier document creates bulk stock and physical tray assets."""
        tray_item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='API 40 cell tray',
            category=InventoryItem.Category.TRAY,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
            base_unit=UnitCode.EACH,
        )
        tray_model = self.tray_model(inventory_item=tray_item)
        tray_line = self.line_payload(
            item=tray_item.pk,
            quantity='2.000000000',
            unit_code=UnitCode.EACH,
            supplier_cost_incl_tax='12.0000',
            input_tax_amount='0.0000',
            tax_treatment='unknown',
            tax_rate='0.0000',
            input_tax_source='none',
        )

        created = self.client.post(
            self.receipt_url,
            self.receipt_payload(lines=[self.line_payload(), tray_line]),
            format='json',
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(StockLot.objects.count(), 0)
        self.assertEqual(InventoryUnit.objects.count(), 0)
        self.assertEqual(SeedTray.objects.count(), 0)
        posted = self.client.post(
            f"{self.receipt_url}{created.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(
            StockLot.objects.filter(
                receipt_line__receipt_id=created.data['pk'],
            ).count(),
            2,
        )
        units = InventoryUnit.objects.filter(item=tray_item).order_by('pk')
        self.assertEqual(units.count(), 2)
        self.assertEqual(
            sum(unit.acquisition_cost for unit in units),
            Decimal('12'),
        )
        self.assertTrue(all(
            unit.current_location_id == self.store.pk for unit in units
        ))
        trays = SeedTray.objects.filter(model=tray_model)
        self.assertEqual(trays.count(), 2)
        self.assertTrue(all(
            SeedTrayCell.objects.filter(tray=tray).count() == 40
            for tray in trays
        ))
        self.assertEqual(
            StockMovement.objects.filter(
                receipt_line__receipt_id=created.data['pk'],
                movement_type=StockMovement.MovementType.RECEIPT,
            ).count(),
            3,
        )

    def test_mapped_seed_tray_lines_require_exact_whole_counts(self):
        """Every saved tray count must be able to become physical identities."""
        tray_model = self.tray_model(identifier='API-exact-tray')
        base = self.line_payload(
            item=tray_model.inventory_item_id,
            unit_code=UnitCode.EACH,
        )
        invalid_lines = (
            ('fractional', {**base, 'quantity': '1.500000000'}),
            ('estimated', {**base, 'quantity_certainty': 'estimated'}),
            ('unknown', {
                **base,
                'quantity': None,
                'quantity_certainty': 'unknown',
            }),
        )
        for label, line in invalid_lines:
            with self.subTest(quantity=label):
                rejected = self.client.post(
                    self.receipt_url,
                    self.receipt_payload(lines=[line]),
                    format='json',
                )
                self.assertEqual(rejected.status_code, 400, rejected.data)
