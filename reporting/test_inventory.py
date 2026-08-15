"""Contracts for versioned, workspace-safe inventory reports."""

# Test names state their contracts more clearly than repeated docstrings.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from inventory.models import (
    StockMovement,
    Stocktake,
    StocktakeTarget,
    StocktakeVariance,
)
from sales.models import SalesOrder, SalesOrderAllocation, SalesOrderLine
from tests.factories import make_inventory_item, make_location, make_seed_tray, make_stock_lot
from workspaces.models import get_current_workspace


class InventoryReportTests(APITestCase):
    """Inventory JSON and CSV share exact derived rows and warnings."""

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='reporter')
        self.client.force_authenticate(self.user)
        self.location = make_location(workspace=self.workspace)

    def test_balance_reconciles_reservations_and_unknown_cost(self):
        tray = make_seed_tray(workspace=self.workspace)
        unit = tray.inventory_unit
        order = SalesOrder.objects.create(
            workspace=self.workspace,
            order_number='SO-REPORT',
            status=SalesOrder.Status.DRAFT,
            order_date=date(2026, 8, 1),
            currency_code=self.workspace.currency_code,
        )
        line = SalesOrderLine.objects.create(
            order=order,
            line_type=SalesOrderLine.LineType.TRAY,
            tray_item=unit.item,
            description='Tray',
            quantity=1,
            unit_price=Decimal('0'),
            tax_rate=Decimal('0'),
        )
        SalesOrderAllocation.objects.create(
            line=line,
            inventory_unit=unit,
            status=SalesOrderAllocation.Status.RESERVED,
            created_by=self.user,
        )
        response = self.client.get('/reports/inventory-balances/', {
            'lot': unit.source_lot_id,
        })
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['results'][0]
        self.assertEqual(row['physical_quantity'], '1.000000000')
        self.assertEqual(row['reserved_quantity'], '1.000000000')
        self.assertEqual(row['available_quantity'], '0.000000000')
        self.assertEqual(response.data['reconciliation']['quantity_equation'], 'physical = reserved + available')

        unknown_item = make_inventory_item(
            workspace=self.workspace,
            name='Unknown legacy medium',
        )
        make_stock_lot(
            workspace=self.workspace,
            item=unknown_item,
            location=self.location,
            base_unit_cost=None,
            acquisition_total=None,
        )
        response = self.client.get('/reports/inventory-balances/', {
            'item': unknown_item.pk,
        })
        self.assertIsNone(response.data['results'][0]['physical_value'])
        self.assertEqual(response.data['data_quality'][0]['code'], 'unvalued_inventory')

    def test_csv_uses_same_filters_and_stable_headers(self):
        item = make_inventory_item(workspace=self.workspace, name='Compost')
        lot = make_stock_lot(
            workspace=self.workspace, item=item, location=self.location,
            quantity=Decimal('5'), base_unit_cost=Decimal('2'),
            acquisition_total=Decimal('10'),
        )
        response = self.client.get('/reports/inventory-balances/export/', {
            'lot': lot.pk,
        })
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('report,version,generated_at,filters', content)
        self.assertIn('inventory-balances,nursery-reports.v1', content)
        self.assertIn('item_id,item_name,lot_id,lot_identifier', content)
        self.assertIn(lot.identifier, content)

    def test_movement_filters_paginate_without_changing_totals(self):
        lot = make_stock_lot(
            workspace=self.workspace, location=self.location,
            quantity=Decimal('3'),
        )
        StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            movement_type=StockMovement.MovementType.CONSUMPTION,
            quantity=Decimal('1'),
            source=self.location,
            occurred_at=timezone.now(),
            reference='REPORT-CONSUME',
        )
        response = self.client.get('/reports/inventory-movements/', {
            'lot': lot.pk, 'page_size': 1,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['totals']['movements'], 2)
        self.assertIsNotNone(response.data['next'])
        bad = self.client.get('/reports/inventory-movements/', {'movment_type': 'sale'})
        self.assertEqual(bad.status_code, 400)

    def test_serialized_tray_and_stocktake_variance_are_traceable(self):
        tray = make_seed_tray(workspace=self.workspace)
        response = self.client.get('/reports/serialized-trays/', {
            'item': tray.inventory_unit.item_id,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'][0]['tray_id'], tray.pk)
        self.assertEqual(response.data['results'][0]['source_lot_id'], tray.inventory_unit.source_lot_id)

        stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            status=Stocktake.Status.OPEN,
            counted_at=timezone.now(),
            created_by=self.user,
        )
        target = StocktakeTarget.objects.create(
            stocktake=stocktake,
            target_type=StocktakeTarget.TargetType.TRAY,
            target_key=f'tray:{tray.pk}',
            target_object_id=tray.pk,
            display='Test tray',
            expected_location=tray.inventory_unit.current_location,
            expected_state='available',
            expected_snapshot={'state': 'available'},
            source_revision='revision',
        )
        StocktakeVariance.objects.create(
            target=target,
            kind=StocktakeVariance.Kind.STATE,
            expected={'state': 'available'},
            observed={'state': 'missing'},
        )
        response = self.client.get('/reports/stocktake-variances/', {
            'stocktake': stocktake.pk,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'][0]['target_id'], tray.pk)
        self.assertEqual(response.data['totals']['unresolved'], 1)

    def test_reports_require_nursery_mode_and_authentication(self):
        self.workspace.mode = self.workspace.Mode.GARDEN
        self.workspace.save(update_fields=['mode'])
        response = self.client.get('/reports/inventory-balances/')
        self.assertEqual(response.status_code, 403)
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.client.force_authenticate(user=None)
        response = self.client.get('/reports/inventory-balances/')
        self.assertEqual(response.status_code, 403)
