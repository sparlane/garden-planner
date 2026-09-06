"""Measured commerce shares nursery posting, locks, and monetary history."""

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError

from inventory.ledger import bulk_balance, unpromised_bulk
from inventory.models import InventoryItem
from inventory.units import UnitCode
from tests.factories import make_stock_lot

from .calculations import LineAmounts
from .commerce import (
    order_commerce_summary, post_fulfillment, post_refund, post_return,
    record_payment, reverse_fulfillment, reverse_return,
)
from .models import Payment, SalesOrderAllocation, SalesReturnLine
from .services import LotRequest, allocate_targets, confirm_order
from .test_counted_lines import CountedStockTestCase


class MeasuredCommerceTests(CountedStockTestCase):
    """A measured lot can serve several customers without new commerce models."""

    def setUp(self):
        super().setUp()
        self.item = InventoryItem.objects.create(
            workspace=self.workspace, name='Measured growing medium',
            category=InventoryItem.Category.GROWING_MEDIA,
            tracking_mode=InventoryItem.TrackingMode.LOT, base_unit=UnitCode.LITRE,
        )
        self.lot = make_stock_lot(item=self.item, location=self.store, quantity=Decimal('12.4'),
                                  base_unit_cost=Decimal('0.5123'), acquisition_total=Decimal('6.3525'))

    def reserve(self, quantity):
        """Confirm a decimal promise against the shared lot."""
        line = self.counted_line(quantity=Decimal(quantity), unit=UnitCode.LITRE)
        allocation = allocate_targets(line, self.user, lot_requests=[
            LotRequest(self.lot.pk, self.store.pk, Decimal(quantity)),
        ])[0]
        confirm_order(line.order, self.user)
        return line, allocation

    def dispatch(self, line, allocation, quantity, **kwargs):
        """Dispatch a specified measured part of one promise."""
        return post_fulfillment(
            line.order, self.user, operation_key=kwargs.pop('operation_key', uuid4()),
            allocation_ids=[allocation.pk], quantities={allocation.pk: Decimal(quantity)},
            **kwargs,
        )

    def give_back(self, line, fulfillment_line, quantity):
        """Return part of a measured dispatch to its original store."""
        return post_return(
            line.order, self.user, operation_key=uuid4(), reason='Unused medium',
            items=[{'fulfillment_line': fulfillment_line, 'quantity': Decimal(quantity),
                    'outcome': SalesReturnLine.Outcome.AVAILABLE, 'destination': self.store}],
        )

    def test_three_customers_partial_dispatch_return_and_refund(self):
        """Stock, fulfillment, and cash ceilings refer to quantities and value."""
        first, first_claim = self.reserve('4.1')
        second, second_claim = self.reserve('3.2')
        self.reserve('5.1')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
        first_dispatch = self.dispatch(first, first_claim, '1.25')
        self.dispatch(second, second_claim, '0.7')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
        first_claim.refresh_from_db()
        self.assertEqual(first_claim.status, SalesOrderAllocation.Status.RESERVED)
        sold = first_dispatch.lines.get()
        self.assertEqual(sold.quantity, Decimal('1.25'))
        self.assertEqual(sold.unit, UnitCode.LITRE)
        self.assertEqual(sold.cogs_amount, Decimal('0.6404'))
        self.give_back(first, sold, '0.2')
        self.give_back(first, sold, '0.15')
        self.assertEqual(unpromised_bulk(self.lot, self.store), Decimal('0.35'))
        summary = order_commerce_summary(first.order)
        self.assertEqual(summary['reserved_quantity'], Decimal('2.85'))
        self.assertEqual(summary['fulfilled_quantity'], Decimal('1.25'))
        self.assertEqual(summary['returned_quantity'], Decimal('0.35'))
        payment = record_payment(
            first.order, self.user, operation_key=uuid4(), amount=Decimal('2'),
            method=Payment.Method.CASH, paid_on=first.order.order_date,
        )
        for amount in ['0.1', '0.2']:
            post_refund(first.order, self.user, operation_key=uuid4(), payment=payment,
                        fulfillment_lines=[sold], amount=Decimal(amount), reason='Adjustment')
        with self.assertRaises(ValidationError):
            post_refund(first.order, self.user, operation_key=uuid4(), payment=payment,
                        fulfillment_lines=[sold], amount=sold.total_incl_tax, reason='Too much')
        with self.assertRaises(ValidationError):
            self.give_back(first, sold, '1')
        with self.assertRaises(ValidationError):
            self.dispatch(first, first_claim, '2.850000001')

    def test_fractional_parts_reconcile_to_every_stored_money_component(self):
        """The final quantity absorbs rounding without restating prior dispatches."""
        line, claim = self.reserve('4.1')
        rows = [self.dispatch(line, claim, quantity).lines.get() for quantity in ['0.0001', '1.2499', '2.85']]
        for field in LineAmounts._fields:
            self.assertEqual(sum(getattr(row, field) for row in rows), getattr(line, field))
        claim.refresh_from_db()
        self.assertEqual(claim.status, SalesOrderAllocation.Status.FULFILLED)

    def test_partial_reversals_restore_only_the_reversed_quantity(self):
        """A reversal preserves the other dispatch and restores its own hold."""
        line, claim = self.reserve('4.1')
        first = self.dispatch(line, claim, '1.25')
        second = self.dispatch(line, claim, '2.85')
        returned = self.give_back(line, first.lines.get(), '0.2')
        reverse_return(returned, self.user, operation_key=uuid4(), reason='Correction')
        reverse_fulfillment(second, self.user, operation_key=uuid4(), reason='Correction')
        summary = order_commerce_summary(line.order)
        self.assertEqual(summary['reserved_quantity'], Decimal('2.85'))
        self.assertEqual(summary['fulfilled_quantity'], Decimal('1.25'))
        self.assertEqual(summary['returned_quantity'], 0)
        self.assertEqual(bulk_balance(self.lot, self.store), Decimal('11.15'))
        self.assertEqual(unpromised_bulk(self.lot, self.store), Decimal('8.3'))

    def test_partial_dispatch_retry_is_idempotent(self):
        """A repeated measured request cannot consume another part of the lot."""
        line, claim = self.reserve('4.1')
        key = uuid4()
        first = self.dispatch(line, claim, '1.25', operation_key=key)
        again = self.dispatch(line, claim, '1.25', operation_key=key)
        self.assertEqual(first.pk, again.pk)
        with self.assertRaises(ValidationError):
            self.dispatch(line, claim, '1.26', operation_key=key)

    def test_unit_and_precision_are_checked_before_posting(self):
        """An incompatible unit or silently rounded request cannot move stock."""
        with self.assertRaises(ValidationError):
            self.counted_line(quantity=Decimal('1.2'), unit=UnitCode.KILOGRAM)
        line, claim = self.reserve('4.1')
        for quantity in ['0', '-1', 'NaN', 'Infinity', '0.0000000001']:
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                self.dispatch(line, claim, quantity)
        self.assertEqual(bulk_balance(self.lot, self.store), Decimal('12.4'))

    def test_rest_contract_exposes_exact_quantities_and_units(self):
        """Fractional inputs and snapshots survive the REST boundary exactly."""
        response = self.client.post('/sales/order-lines/', {
            'order': self.counted_line(quantity=1, unit=UnitCode.LITRE).order_id,
            'line_type': 'lot_quantity', 'item': self.item.pk,
            'description': 'Measured medium', 'quantity': '0.123456789',
            'unit': 'l', 'unit_price': '0.8', 'tax_rate': '15',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['quantity'], '0.123456789')
        self.assertEqual(response.json()['unit'], 'l')

    def test_partial_return_can_be_replaced_without_overpromising_the_order(self):
        """A replacement covers what came back alongside the original remainder."""
        line, claim = self.reserve('4.1')
        sold = self.dispatch(line, claim, '1.25').lines.get()
        self.give_back(line, sold, '0.2')
        replacement = allocate_targets(line, self.user, lot_requests=[
            LotRequest(self.lot.pk, self.store.pk, Decimal('0.2')),
        ])[0]
        with self.assertRaises(ValidationError):
            allocate_targets(line, self.user, lot_requests=[
                LotRequest(self.lot.pk, self.store.pk, Decimal('0.000000001')),
            ])
        self.dispatch(line, replacement, '0.2')
        self.dispatch(line, claim, '2.85')
        line.order.refresh_from_db()
        self.assertEqual(line.order.status, 'fulfilled')

    def test_many_partial_returns_restore_exactly_the_original_cost(self):
        """Rounded return cost shares reconcile without editing earlier rows."""
        line, claim = self.reserve('4.1')
        sold = self.dispatch(line, claim, '1.25').lines.get()
        returns = [self.give_back(line, sold, quantity).lines.get() for quantity in ['0.0001', '0.7', '0.5499']]
        self.assertEqual(sum(row.cogs_amount for row in returns), sold.cogs_amount)

    def test_return_reversal_cannot_take_another_customers_reservation(self):
        """Positive physical stock is insufficient if somebody else holds it."""
        line, claim = self.reserve('4.1')
        sold = self.dispatch(line, claim, '1.25').lines.get()
        returned = self.give_back(line, sold, '0.2')
        self.reserve('8.5')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
        with self.assertRaises(ValidationError):
            reverse_return(returned, self.user, operation_key=uuid4(), reason='Correction')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
        self.assertEqual(order_commerce_summary(line.order)['returned_quantity'], Decimal('0.2'))
