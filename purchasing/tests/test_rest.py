"""End-to-end API contracts for purchasing and supplier payables."""

# pylint: disable=duplicate-code

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from inventory.models import StockReceipt
from inventory.units import UnitCode
from tests.factories import make_plant_variety, make_supplier
from tax.entries import AWAITING_PAYMENT, PURCHASE, derive_entries
from tax.models import GstRegistration
from tax.services import record_registration
from workspaces.models import get_current_workspace


class PurchasingRestTests(APITestCase):
    """Purchases reconcile commitments, received seed, liabilities, and cash."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='purchasing-api-user')
        self.client.force_authenticate(self.user)
        self.supplier = make_supplier(workspace=self.workspace, name='Seed merchant')
        variety = make_plant_variety(workspace=self.workspace)
        catalog = self.client.post('/seeds/seeds/', {
            'supplier': self.supplier.pk,
            'plant_variety': variety.pk,
            'supplier_code': 'CARROT-1',
            'base_unit': UnitCode.SEED,
        }, format='json')
        self.assertEqual(catalog.status_code, 201, catalog.data)
        self.seed_catalog = catalog.data

    def receive_seed(self, quantity='40', reference='DEL-1', received_date='2026-08-20'):
        """Receive one exact packet and return its posted receipt line."""
        draft = self.client.post('/seeds/packet-receipts/', {
            'seeds': self.seed_catalog['pk'],
            'supplier': self.supplier.pk,
            'quantity_certainty': 'exact',
            'quantity': quantity,
            'line_price': '11.5000',
            'supplier_cost_incl_tax': '11.5000',
            'tax_treatment': 'standard',
            'tax_rate': '15.0000',
            'input_tax_source': 'supplier',
            'input_tax_amount': '1.5000',
            'claim_input_tax': True,
            'claimable_percentage': '100.0000',
            'received_date': received_date,
            'supplier_reference': reference,
        }, format='json')
        self.assertEqual(draft.status_code, 201, draft.data)
        posted = self.client.post(
            f"/seeds/packet-receipts/{draft.data['pk']}/post/", {}, format='json',
        )
        self.assertEqual(posted.status_code, 201, posted.data)
        receipt = StockReceipt.objects.get(seed_packet_draft__pk=draft.data['pk'])
        return receipt.lines.get()

    def create_invoice(self, receipt_line, reference='INV-100'):
        """Create and confirm an invoice for a received packet."""
        created = self.client.post('/purchasing/invoices/', {
            'supplier': self.supplier.pk,
            'external_reference': reference,
            'invoice_date': '2026-08-21',
            'due_date': '2026-09-20',
            'currency_code': 'NZD',
            'lines': [{
                'description': 'Received carrot seed',
                'receipt_line': receipt_line.pk,
                'is_freight': False,
                'subtotal_ex_tax': '10.0000',
                'tax_rate': '15.0000',
                'tax_total': '1.5000',
                'total_incl_tax': '11.5000',
            }],
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        confirmed = self.client.post(
            f"/purchasing/invoices/{created.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        return confirmed.data

    def pay(self, invoice, amount, reference):
        """Create one allocated supplier payment."""
        response = self.client.post('/purchasing/payments/', {
            'supplier': self.supplier.pk,
            'paid_on': '2026-08-25',
            'amount': amount,
            'currency_code': 'NZD',
            'method': 'bank_transfer',
            'external_reference': reference,
            'allocations': [{'invoice': invoice, 'amount': amount}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_existing_received_seed_can_be_invoiced_and_part_paid(self):
        """The requested after-the-fact seed-invoice pathway is supported."""
        receipt_line = self.receive_seed()
        lot = receipt_line.stock_lot
        original_cost = lot.acquisition_total

        invoice = self.create_invoice(receipt_line)

        self.assertEqual(invoice['supplier_name_snapshot'], self.supplier.name)
        self.assertEqual(invoice['state']['payment_state'], 'unpaid')
        self.assertEqual(invoice['state']['balance_due'], Decimal('11.5000'))
        self.pay(invoice['pk'], '5.0000', 'PAY-1')
        part_paid = self.client.get(f"/purchasing/invoices/{invoice['pk']}/")
        self.assertEqual(part_paid.data['state']['payment_state'], 'part_paid')
        self.assertEqual(part_paid.data['state']['balance_due'], Decimal('6.5000'))
        self.pay(invoice['pk'], '6.5000', 'PAY-2')
        paid = self.client.get(f"/purchasing/invoices/{invoice['pk']}/")
        self.assertEqual(paid.data['state']['payment_state'], 'paid')
        summary = self.client.get('/purchasing/summary/', {'as_of': '2026-09-21'})
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data['cash_paid'], Decimal('11.5000'))
        self.assertFalse(any(
            warning['source_id'] == receipt_line.pk and warning['code'] == 'receipt_not_invoiced'
            for warning in summary.data['warnings']
        ))

        lot.refresh_from_db()
        self.assertEqual(lot.acquisition_total, original_cost)

    def test_payments_basis_claims_partial_invoice_payments_proportionally(self):
        """A part-paid seed invoice claims only the discharged share of input tax."""
        record_registration(
            self.workspace, self.user,
            registered=True,
            gst_number='123456785',
            basis=GstRegistration.Basis.PAYMENTS,
            filing_frequency=GstRegistration.Frequency.MONTHLY,
            period_anchor_month=1,
            effective_from=date(2026, 1, 1),
        )
        invoice = self.create_invoice(self.receive_seed())
        self.pay(invoice['pk'], '5.0000', 'PAY-PART')

        entries = derive_entries(
            self.workspace,
            date(2026, 8, 1),
            date(2026, 8, 31),
        )
        purchases = [entry for entry in entries if entry.kind == PURCHASE]
        claimed = [entry for entry in purchases if not entry.exclusion]
        awaiting = [entry for entry in purchases if entry.exclusion == AWAITING_PAYMENT]
        self.assertEqual(sum(entry.tax for entry in claimed), Decimal('0.6522'))
        self.assertEqual(sum(entry.tax for entry in awaiting), Decimal('0.8478'))
        self.assertEqual({entry.source_type for entry in claimed}, {'supplier_payment'})

    def test_invoice_basis_uses_confirmed_supplier_invoice_date(self):
        """A later supplier invoice supersedes the receipt-date proxy."""
        record_registration(
            self.workspace, self.user,
            registered=True,
            gst_number='123456785',
            basis=GstRegistration.Basis.INVOICE,
            filing_frequency=GstRegistration.Frequency.MONTHLY,
            period_anchor_month=1,
            effective_from=date(2026, 1, 1),
        )
        self.create_invoice(self.receive_seed(received_date='2026-07-31'))

        entries = derive_entries(self.workspace, date(2026, 7, 1), date(2026, 8, 31))
        purchases = [entry for entry in entries if entry.kind == PURCHASE]
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0].supply_date, date(2026, 8, 21))
        self.assertEqual(purchases[0].source_type, 'supplier_invoice')
        self.assertFalse(purchases[0].proxy)

    def test_partial_deliveries_and_over_delivery_remain_visible(self):
        """Receipt matching reports each commercial quantity independently."""
        first = self.receive_seed('40', 'DEL-40')
        second = self.receive_seed('80', 'DEL-80')
        order = self.client.post('/purchasing/orders/', {
            'order_number': 'PO-100',
            'supplier': self.supplier.pk,
            'ordered_on': '2026-08-15',
            'expected_on': '2026-08-20',
            'currency_code': 'NZD',
            'lines': [{
                'item': first.item_id,
                'description': 'Carrot seed',
                'quantity': '100.000000000',
                'unit_code': UnitCode.SEED,
                'unit_price_ex_tax': '0.1000',
                'tax_rate': '15.0000',
                'freight_ex_tax': '2.0000',
            }],
        }, format='json')
        self.assertEqual(order.status_code, 201, order.data)
        confirmed = self.client.post(
            f"/purchasing/orders/{order.data['pk']}/confirm/", {}, format='json',
        )
        line = confirmed.data['lines'][0]
        for receipt_line, quantity in ((first, '40'), (second, '80')):
            matched = self.client.post(
                f"/purchasing/orders/{order.data['pk']}/match-receipt/",
                {
                    'order_line': line['pk'],
                    'receipt_line': receipt_line.pk,
                    'base_quantity': quantity,
                },
                format='json',
            )
            self.assertEqual(matched.status_code, 201, matched.data)
        refreshed = self.client.get(f"/purchasing/orders/{order.data['pk']}/")
        state = refreshed.data['lines'][0]['state']
        self.assertEqual(state['received'], Decimal('120'))
        self.assertEqual(state['outstanding'], Decimal('0'))
        self.assertEqual(state['over_received'], Decimal('20'))

    def test_reviewed_requisition_converts_to_a_supplier_order(self):
        """A reviewed material need becomes a traceable commercial line."""
        item = self.receive_seed().item
        requisition = self.client.post('/purchasing/requisitions/', {
            'item': item.pk,
            'required_on': '2026-09-10',
            'quantity': '200.000000000',
            'unit_code': UnitCode.SEED,
            'preferred_supplier': self.supplier.pk,
            'estimated_total_incl_tax': '23.0000',
            'notes': 'Reviewed shortage',
        }, format='json')
        self.assertEqual(requisition.status_code, 201, requisition.data)
        reviewed = self.client.post(
            f"/purchasing/requisitions/{requisition.data['pk']}/review/", {}, format='json',
        )
        self.assertEqual(reviewed.data['status'], 'reviewed')
        order = self.client.post(
            f"/purchasing/requisitions/{requisition.data['pk']}/order/",
            {
                'order_number': 'PO-REQ-1',
                'supplier': self.supplier.pk,
                'ordered_on': '2026-08-26',
                'expected_on': '2026-09-10',
                'currency_code': 'NZD',
                'unit_price_ex_tax': '0.1000',
                'tax_rate': '15.0000',
                'freight_ex_tax': '0.0000',
                'notes': '',
            },
            format='json',
        )
        self.assertEqual(order.status_code, 201, order.data)
        self.assertEqual(order.data['lines'][0]['requisition'], requisition.data['pk'])
        refreshed = self.client.get(f"/purchasing/requisitions/{requisition.data['pk']}/")
        self.assertEqual(refreshed.data['status'], 'ordered')

    def test_credit_and_payment_reversal_preserve_original_records(self):
        """Corrections are linked acts, never destructive edits."""
        invoice = self.create_invoice(self.receive_seed())
        credit = self.client.post(f"/purchasing/invoices/{invoice['pk']}/correct/", {
            'kind': 'credit',
            'external_reference': 'CR-100',
            'corrected_on': '2026-08-22',
            'subtotal_ex_tax': '2.0000',
            'tax_total': '0.3000',
            'total_incl_tax': '2.3000',
            'reason': 'Damaged seed packet',
        }, format='json')
        self.assertEqual(credit.status_code, 201, credit.data)
        payment = self.pay(invoice['pk'], '9.2000', 'PAY-CORRECTED')
        reversal = self.client.post(
            f"/purchasing/payments/{payment['pk']}/reverse/",
            {'reason': 'Wrong bank transaction'}, format='json',
        )
        self.assertEqual(reversal.status_code, 201, reversal.data)
        state = self.client.get(f"/purchasing/invoices/{invoice['pk']}/").data['state']
        self.assertEqual(state['net_total'], Decimal('9.2000'))
        self.assertEqual(state['payment_state'], 'unpaid')

    def test_non_stock_expense_is_confirmed_without_inventory(self):
        """Business costs retain category, payee, tax, and payment context."""
        category = self.client.post('/purchasing/expense-categories/', {
            'name': 'Market fees', 'notes': '', 'active': True,
        }, format='json')
        self.assertEqual(category.status_code, 201, category.data)
        expense = self.client.post('/purchasing/expenses/', {
            'category': category.data['pk'],
            'payee': 'Saturday market',
            'incurred_on': '2026-08-22',
            'paid_on': '2026-08-23',
            'currency_code': 'NZD',
            'subtotal_ex_tax': '20.0000',
            'tax_total': '3.0000',
            'total_incl_tax': '23.0000',
            'allocation_type': 'market',
            'allocation_reference': 'Riverside 2026-08-22',
        }, format='json')
        self.assertEqual(expense.status_code, 201, expense.data)
        confirmed = self.client.post(
            f"/purchasing/expenses/{expense.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['status'], 'confirmed')
        self.assertEqual(confirmed.data['payment_state'], 'paid')
        summary = self.client.get('/purchasing/summary/', {'as_of': '2026-08-26'})
        self.assertEqual(summary.data['expenses']['total_incl_tax'], Decimal('23.0000'))
        self.assertEqual(summary.data['cash_paid'], Decimal('23.0000'))

    def expense_category(self, name='Tools'):
        """Create one reusable expense classification."""
        category = self.client.post('/purchasing/expense-categories/', {
            'name': name, 'notes': '', 'active': True,
        }, format='json')
        self.assertEqual(category.status_code, 201, category.data)
        return category.data['pk']

    def test_backdated_expense_keeps_its_own_date_and_claimed_gst(self):
        """A cost entered days later is dated when it happened, not today."""
        expense = self.client.post('/purchasing/expenses/', {
            'category': self.expense_category(),
            'payee': 'Hardware store',
            'incurred_on': '2026-08-14',
            'paid_on': '2026-08-14',
            'currency_code': 'NZD',
            'subtotal_ex_tax': '400.0000',
            'tax_total': '60.0000',
            'total_incl_tax': '460.0000',
            'tax_treatment': 'standard',
            'claim_input_tax': True,
            'claimable_percentage': '100.0000',
        }, format='json')
        self.assertEqual(expense.status_code, 201, expense.data)
        confirmed = self.client.post(
            f"/purchasing/expenses/{expense.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(str(confirmed.data['incurred_on']), '2026-08-14')
        self.assertEqual(Decimal(confirmed.data['recoverable_tax']), Decimal('60.0000'))
        self.assertEqual(Decimal(confirmed.data['deductible_amount']), Decimal('400.0000'))

    def test_unclaimed_expense_deducts_the_whole_gst_inclusive_cost(self):
        """Without an input-tax claim the GST stays in the income-tax cost."""
        expense = self.client.post('/purchasing/expenses/', {
            'category': self.expense_category('Insurance'),
            'payee': 'Insurer',
            'incurred_on': '2026-08-10',
            'currency_code': 'NZD',
            'subtotal_ex_tax': '200.0000',
            'tax_total': '30.0000',
            'total_incl_tax': '230.0000',
            'tax_treatment': 'standard',
            'claim_input_tax': False,
            'claimable_percentage': '0.0000',
        }, format='json')
        self.assertEqual(expense.status_code, 201, expense.data)
        confirmed = self.client.post(
            f"/purchasing/expenses/{expense.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(Decimal(confirmed.data['recoverable_tax']), Decimal('0.0000'))
        self.assertEqual(Decimal(confirmed.data['deductible_amount']), Decimal('230.0000'))

    def test_apportioned_expense_claim_requires_a_stated_basis(self):
        """A partial claim is rejected as a field error until it is explained."""
        payload = {
            'category': self.expense_category('Power'),
            'payee': 'Lines company',
            'incurred_on': '2026-08-12',
            'currency_code': 'NZD',
            'subtotal_ex_tax': '100.0000',
            'tax_total': '15.0000',
            'total_incl_tax': '115.0000',
            'tax_treatment': 'standard',
            'claim_input_tax': True,
            'claimable_percentage': '60.0000',
        }
        rejected = self.client.post('/purchasing/expenses/', payload, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('apportionment_basis', rejected.data)
        accepted = self.client.post('/purchasing/expenses/', dict(
            payload, apportionment_basis='Sixty percent of the meter runs the packhouse',
        ), format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)
        confirmed = self.client.post(
            f"/purchasing/expenses/{accepted.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(Decimal(confirmed.data['recoverable_tax']), Decimal('9.0000'))
        self.assertEqual(Decimal(confirmed.data['deductible_amount']), Decimal('106.0000'))

    def test_expense_totals_must_reconcile(self):
        """A mistyped total is a field error rather than a stored inconsistency."""
        rejected = self.client.post('/purchasing/expenses/', {
            'category': self.expense_category('Freight'),
            'payee': 'Courier',
            'incurred_on': '2026-08-12',
            'currency_code': 'NZD',
            'subtotal_ex_tax': '100.0000',
            'tax_total': '15.0000',
            'total_incl_tax': '120.0000',
            'tax_treatment': 'standard',
        }, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('total_incl_tax', rejected.data)

    def test_collections_require_authentication(self):
        """Purchasing data is not exposed anonymously."""
        self.client.force_authenticate(user=None)
        for path in (
                '/purchasing/requisitions/', '/purchasing/orders/',
                '/purchasing/invoices/', '/purchasing/payments/',
                '/purchasing/expenses/'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
