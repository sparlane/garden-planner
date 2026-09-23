"""Inbound freight follows receipt goods into their exact lot costs."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .ledger import post_receipt, reverse_receipt
from .models import StockLot, StockReceipt
from .test_ledger import LedgerFixtureTestCase
from .test_ledger_rest import LedgerRestFixture
from .test_serialized import SerializedInventoryTestCase


class ReceiptFreightTests(LedgerFixtureTestCase):
    """Freight adds once, without changing goods tax or prior receipts."""

    def test_freight_splits_by_acquisition_value_after_recoverable_tax(self):
        """Recoverable tax does not affect the freight allocation basis."""
        receipt = self.make_receipt(freight_acquisition_amount=Decimal('3'))
        first = self.add_receipt_line(receipt, claim_input_tax=True, claimable_percentage=Decimal('100'))
        second = self.add_receipt_line(receipt, supplier_cost_incl_tax=Decimal('23'), input_tax_amount=Decimal('3'), claim_input_tax=True, claimable_percentage=Decimal('100'))
        _, lots = post_receipt(receipt, self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual([first.allocated_freight, second.allocated_freight], [Decimal('1'), Decimal('2')])
        self.assertEqual([lot.acquisition_total for lot in lots], [Decimal('11'), Decimal('22')])
        self.assertEqual(first.recoverable_input_tax, Decimal('1.5'))
        self.assertEqual(lots[0].base_unit_cost, Decimal('0.0055'))
        with self.assertRaises(ValidationError):
            post_receipt(receipt, self.user)

    def test_rounding_reconciles_to_the_freight_document(self):
        """Even a fractional freight charge is preserved exactly once."""
        receipt = self.make_receipt(freight_acquisition_amount=Decimal('0.0001'))
        for _ in range(3):
            self.add_receipt_line(receipt)
        _, lots = post_receipt(receipt, self.user)
        self.assertEqual(sum(line.allocated_freight for line in receipt.lines.all()), Decimal('0.0001'))
        self.assertEqual(sum(lot.acquisition_total for lot in lots), Decimal('34.5001'))

    def test_multiple_free_goods_need_a_freight_allocation_basis(self):
        """Refuse an arbitrary split without refusing free stock."""
        receipt = self.make_receipt(freight_acquisition_amount=Decimal('1'))
        for _ in range(2):
            self.add_receipt_line(receipt, supplier_cost_incl_tax=Decimal('0'), input_tax_amount=Decimal('0'), input_tax_source='none', line_cost_ex_tax=Decimal('0'))
        with self.assertRaisesMessage(ValidationError, 'separate single-line receipts'):
            post_receipt(receipt, self.user)
        self.assertFalse(StockLot.objects.filter(receipt_line__receipt=receipt).exists())
        receipt.freight_acquisition_amount = Decimal('0')
        receipt.save()
        _, lots = post_receipt(receipt, self.user)
        self.assertEqual(lots[0].acquisition_total, Decimal('0'))

    def test_one_free_goods_line_carries_its_freight(self):
        """Donated inputs can have a delivery cost without inventing a price."""
        receipt = self.make_receipt(freight_acquisition_amount=Decimal('2'))
        self.add_receipt_line(receipt, supplier_cost_incl_tax=Decimal('0'), input_tax_amount=Decimal('0'), input_tax_source='none', line_cost_ex_tax=Decimal('0'))
        _, lots = post_receipt(receipt, self.user)
        self.assertEqual(lots[0].acquisition_total, Decimal('2'))

    def test_reversal_retains_the_original_freight_audit(self):
        """Undoing a receipt preserves the explanation of its original value."""
        receipt = self.make_receipt(freight_acquisition_amount=Decimal('2'))
        line = self.add_receipt_line(receipt)
        posted, _ = post_receipt(receipt, self.user)
        reverse_receipt(posted, self.user, 'Wrong delivery')
        posted.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(posted.status, StockReceipt.Status.REVERSED)
        self.assertEqual(line.allocated_freight, Decimal('2'))


class ReceiptFreightRestTests(LedgerRestFixture):
    """The receiving workflow records freight once and exposes posted shares."""

    def test_create_post_and_refuse_edit(self):
        """Only posting can assign shares, and posting freezes them."""
        payload = self.receipt_payload(freight_acquisition_amount='2.0000')
        payload['lines'][0]['allocated_freight'] = '999'
        created = self.client.post(self.receipt_url, payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['lines'][0]['allocated_freight'], '0.0000')
        url = f"{self.receipt_url}{created.data['pk']}/"
        posted = self.client.post(f'{url}post/', {}, format='json')
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(posted.data['lines'][0]['allocated_freight'], '2.0000')
        self.assertEqual(self.client.patch(url, {'freight_acquisition_amount': '3'}, format='json').status_code, 400)

    def test_negative_freight_is_refused(self):
        """An inbound charge cannot silently reduce acquisition cost."""
        response = self.client.post(self.receipt_url, self.receipt_payload(freight_acquisition_amount='-1'), format='json')
        self.assertEqual(response.status_code, 400)


class SerializedFreightTests(SerializedInventoryTestCase):
    """Freight reaches every serialized asset without losing a remainder."""

    def test_numbered_units_reconcile_to_goods_plus_freight(self):
        """Three units share a ten-dollar purchase plus one cent freight."""
        receipt = self.create_receipt(quantity='3', cost='10.0000')
        receipt.freight_acquisition_amount = Decimal('0.01')
        receipt.save()
        _, lots = post_receipt(receipt, self.user)
        costs = list(lots[0].serialized_units.order_by('pk').values_list('acquisition_cost', flat=True))
        self.assertEqual(len(costs), 3)
        self.assertEqual(sum(costs), Decimal('10.01'))
