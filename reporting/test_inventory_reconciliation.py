"""Inventory report figures reconciled against the ledger they derive from."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from inventory.ledger import physical_balance, unit_physical_state
from inventory.models import InventoryUnit, StockLot, StockMovement
from locations.models import Location
from sales.models import SalesOrder, SalesOrderAllocation, SalesOrderLine
from tests.factories import (
    make_inventory_item,
    make_location,
    make_seed_tray,
    make_stock_lot,
)
from workspaces.models import get_current_workspace


class InventoryReconciliationTests(APITestCase):
    """Every published figure adds back to the movements underneath it."""

    balances_url = '/reports/inventory-balances/'
    trays_url = '/reports/serialized-trays/'

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='reconciler')
        self.client.force_authenticate(self.user)
        self.store = make_location(workspace=self.workspace)
        self.growing = make_location(workspace=self.workspace)

    def stocked_item(self, reorder_level=None):
        """Stock one item across two lots, two places, and a partial draw."""
        item = make_inventory_item(
            workspace=self.workspace,
            reorder_level=reorder_level,
        )
        first = make_stock_lot(
            workspace=self.workspace,
            item=item,
            location=self.store,
            quantity=Decimal('60'),
            acquisition_total=Decimal('20'),
            base_unit_cost=Decimal('0.333333333333'),
        )
        second = make_stock_lot(
            workspace=self.workspace,
            item=item,
            location=self.growing,
            quantity=Decimal('40'),
            acquisition_total=Decimal('10'),
            base_unit_cost=Decimal('0.25'),
        )
        self.move(first, Decimal('25'), self.store, self.growing)
        self.move(second, Decimal('40'), self.growing, None)
        return item, first, second

    def move(self, lot, quantity, source, destination):
        """Append one movement without going through a posting service."""
        movement_type = StockMovement.MovementType.TRANSFER
        if destination is None:
            movement_type = StockMovement.MovementType.CONSUMPTION
        return StockMovement.objects.create(
            workspace=self.workspace,
            lot=lot,
            movement_type=movement_type,
            quantity=quantity,
            source=source,
            destination=destination,
            occurred_at=timezone.now(),
        )

    def report(self, url, **query):
        """Return one report body."""
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_every_row_satisfies_the_equation_the_report_publishes(self):
        item, _first, _second = self.stocked_item()
        tray = self.reserved_tray()

        body = self.report(self.balances_url)

        self.assertEqual(
            body['reconciliation']['quantity_equation'],
            'physical = reserved + available',
        )
        rows = [
            row for row in body['results']
            if row['item_id'] in {item.pk, tray.inventory_unit.item_id}
        ]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(lot=row['lot_id'], location=row['location_id']):
                parts = Decimal(row['reserved_quantity']) + Decimal(
                    row['available_quantity'],
                )
                self.assertEqual(Decimal(row['physical_quantity']), parts)

    def test_each_quantity_matches_the_ledger_balance_it_reports(self):
        item, _first, _second = self.stocked_item()

        body = self.report(self.balances_url, item=item.pk)

        self.assertTrue(body['results'])
        for row in body['results']:
            with self.subTest(lot=row['lot_id'], location=row['location_id']):
                self.assertEqual(
                    Decimal(row['physical_quantity']),
                    physical_balance(
                        StockLot.objects.get(pk=row['lot_id']),
                        Location.objects.get(pk=row['location_id']),
                    ),
                )

    def test_a_place_a_lot_has_emptied_is_left_out_rather_than_reported_as_zero(self):
        item, _first, second = self.stocked_item()

        body = self.report(self.balances_url, item=item.pk)

        # The second lot was drawn down to nothing where it stood, and a stock
        # report answers what is on hand -- unlike the balance API, which keeps
        # the lot/location matrix so a screen can show a place going empty.
        self.assertEqual(
            physical_balance(second, self.growing),
            Decimal('0'),
        )
        self.assertNotIn(
            second.pk,
            {row['lot_id'] for row in body['results']},
        )

    def test_totals_are_the_sum_of_the_rows_they_stand_over(self):
        item, _first, _second = self.stocked_item()

        body = self.report(self.balances_url, item=item.pk)

        rows = body['results']
        self.assertTrue(rows)
        for total in body['totals']['quantities']:
            with self.subTest(base_unit=total['base_unit']):
                matching = [
                    row for row in rows if row['base_unit'] == total['base_unit']
                ]
                for column in ('physical', 'reserved', 'available'):
                    self.assertEqual(
                        Decimal(total[column]),
                        sum(
                            Decimal(row[f'{column}_quantity'])
                            for row in matching
                        ),
                    )
        for total in body['totals']['valuations']:
            with self.subTest(currency=total['currency_code']):
                matching = [
                    row for row in rows
                    if row['currency_code'] == total['currency_code']
                    if not row['unvalued']
                ]
                for column in ('physical', 'reserved', 'available'):
                    self.assertEqual(
                        Decimal(total[column]),
                        sum(Decimal(row[f'{column}_value']) for row in matching),
                    )

    def test_each_value_is_its_own_quantity_at_the_lot_cost(self):
        item, _first, _second = self.stocked_item()

        body = self.report(self.balances_url, item=item.pk)

        self.assertTrue(body['results'])
        for row in body['results']:
            with self.subTest(lot=row['lot_id']):
                self.assertFalse(row['unvalued'])
                cost = Decimal(row['unit_cost'])
                for column in ('physical', 'reserved', 'available'):
                    exact = Decimal(row[f'{column}_quantity']) * cost
                    self.assertEqual(
                        Decimal(row[f'{column}_value']),
                        Decimal(f'{exact:.4f}'),
                    )

    def test_a_lot_with_no_cost_reports_no_value_anywhere(self):
        item = make_inventory_item(workspace=self.workspace)
        make_stock_lot(
            workspace=self.workspace,
            item=item,
            location=self.store,
            quantity=Decimal('5'),
            acquisition_total=None,
            base_unit_cost=None,
        )

        body = self.report(self.balances_url, item=item.pk)

        row = body['results'][0]
        self.assertTrue(row['unvalued'])
        self.assertIsNone(row['unit_cost'])
        self.assertIsNone(row['physical_value'])
        self.assertIsNone(row['reserved_value'])
        self.assertIsNone(row['available_value'])
        self.assertEqual(body['totals']['unvalued_rows'], 1)
        self.assertEqual(body['totals']['valuations'], [])

    def test_low_stock_is_judged_on_what_is_left_after_reservations(self):
        tray = self.reserved_tray()
        item = tray.inventory_unit.item
        item.reorder_level = Decimal('1')
        item.save(update_fields=['reorder_level'])

        row = self.lot_row(tray.inventory_unit.source_lot_id)

        # One tray on hand, all of it spoken for: the shelf looks stocked and
        # the nursery has nothing it can sell.
        self.assertEqual(Decimal(row['physical_quantity']), Decimal('1'))
        self.assertEqual(Decimal(row['available_quantity']), Decimal('0'))
        self.assertTrue(row['low_stock'])

    def test_the_reserved_quantity_is_the_count_of_live_reservations(self):
        tray = self.reserved_tray()

        reserved = self.lot_row(tray.inventory_unit.source_lot_id)
        self.assertEqual(Decimal(reserved['reserved_quantity']), Decimal('1'))

        SalesOrderAllocation.objects.filter(
            inventory_unit=tray.inventory_unit,
        ).update(status=SalesOrderAllocation.Status.RELEASED)

        released = self.lot_row(tray.inventory_unit.source_lot_id)
        self.assertEqual(Decimal(released['reserved_quantity']), Decimal('0'))
        self.assertEqual(
            Decimal(released['available_quantity']),
            Decimal(released['physical_quantity']),
        )

    def lot_row(self, lot_id):
        """Return the single balance row one lot produces."""
        body = self.report(self.balances_url, lot=lot_id)
        self.assertEqual(len(body['results']), 1, body['results'])
        return body['results'][0]

    def reserved_tray(self):
        """Reserve one serialized tray against a confirmed-shaped order."""
        tray = make_seed_tray(workspace=self.workspace)
        order = SalesOrder.objects.create(
            workspace=self.workspace,
            order_number=f'SO-TRACE-{tray.pk}',
            status=SalesOrder.Status.DRAFT,
            order_date=timezone.localdate(),
            currency_code=self.workspace.currency_code,
        )
        line = SalesOrderLine.objects.create(**{
            'order': order,
            'line_type': SalesOrderLine.LineType.UNIT,
            'item': tray.inventory_unit.item,
            'description': 'One reserved tray',
            'quantity': 1,
            'unit_price': Decimal('0'),
            'tax_rate': Decimal('0'),
        })
        SalesOrderAllocation.objects.create(**{
            'line': line,
            'inventory_unit': tray.inventory_unit,
            'status': SalesOrderAllocation.Status.RESERVED,
            'created_by': self.user,
        })
        return tray


class SerializedTrayStateTests(APITestCase):
    """The tray report derives state twice; both copies have to agree."""

    trays_url = '/reports/serialized-trays/'

    def setUp(self):
        self.workspace = get_current_workspace()
        self.workspace.mode = self.workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.user = get_user_model().objects.create_user(username='tray-reporter')
        self.client.force_authenticate(self.user)

    def tray_in_state(self, state):
        """Return one tray unit driven into the named physical state."""
        tray = make_seed_tray(workspace=self.workspace)
        unit = tray.inventory_unit
        if state == 'available':
            return unit
        if state == 'quarantined':
            InventoryUnit.objects.filter(pk=unit.pk).update(
                current_location=make_location(
                    workspace=self.workspace,
                    location_type=Location.LocationType.QUARANTINE,
                ),
            )
            return unit
        movement_types = {
            'returned': StockMovement.MovementType.CUSTOMER_RETURN,
            'lost': StockMovement.MovementType.ADJUSTMENT_LOSS,
            'retired': StockMovement.MovementType.WASTE,
            'dispatched': StockMovement.MovementType.SALE,
        }
        keeps_location = state == 'returned'
        StockMovement.objects.create(
            workspace=self.workspace,
            lot=unit.source_lot,
            unit=unit,
            movement_type=movement_types[state],
            quantity=Decimal('1'),
            source=None if keeps_location else unit.current_location,
            destination=unit.current_location if keeps_location else None,
            occurred_at=timezone.now(),
        )
        if not keeps_location:
            InventoryUnit.objects.filter(pk=unit.pk).update(current_location=None)
        return unit

    def test_the_report_agrees_with_the_ledger_on_every_reachable_state(self):
        wanted = {}
        for state in (
                'available', 'quarantined', 'returned',
                'lost', 'retired', 'dispatched',
        ):
            wanted[self.tray_in_state(state).pk] = state

        response = self.client.get(self.trays_url)
        self.assertEqual(response.status_code, 200, response.data)
        rows = {
            row['unit_id']: row for row in response.data['results']
            if row['unit_id'] in wanted
        }

        self.assertEqual(set(rows), set(wanted))
        for unit_id, state in wanted.items():
            with self.subTest(state=state):
                self.assertEqual(rows[unit_id]['physical_state'], state)
                self.assertEqual(
                    rows[unit_id]['physical_state'],
                    unit_physical_state(
                        InventoryUnit.objects.select_related(
                            'current_location',
                        ).get(pk=unit_id),
                    ),
                )

    def test_a_tray_is_available_only_where_it_is_here_and_unspoken_for(self):
        on_hand = self.tray_in_state('available')
        returned = self.tray_in_state('returned')
        gone = self.tray_in_state('dispatched')

        response = self.client.get(self.trays_url)
        rows = {row['unit_id']: row for row in response.data['results']}

        self.assertTrue(rows[on_hand.pk]['available'])
        self.assertTrue(rows[returned.pk]['available'])
        self.assertFalse(rows[gone.pk]['available'])
