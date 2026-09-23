"""Multiple currencies are something a workspace turns on (task 159).

A workspace that trades in one currency is not asked which one every time it
receives a delivery. The switch governs input: while it is off the ten
documents an operator names a currency on take the workspace's own and refuse
any other by name, and everything already recorded in another currency keeps
it -- which is what the last class here checks, against the batch task 142
built.
"""

# pylint: disable=duplicate-code

from datetime import date

from bookkeeping.models import (
    BookkeepingEntry,
    IncomeTaxYear,
    Liability,
    StockValuationLine,
    TaxAsset,
)
from costing.services import batch_cost_breakdown, plant_cost_breakdown
from costing.test_currencies import MixedCurrencyTestCase
from inventory.models import StockReceipt
from inventory.units import UnitCode
from purchasing.models import (
    BusinessExpense,
    PurchaseOrder,
    SupplierInvoice,
    SupplierPayment,
)
from sales.models import SalesOrder
from tests.api import RESTContractTestCase
from tests.factories import (
    make_expense_category,
    make_inventory_item,
    make_location,
    make_nursery_workspace,
    make_supplier,
)

from .currency import currency_input_refusal
from .models import Workspace, get_current_workspace


def refusal_for(code):
    """What a workspace recording only New Zealand dollars says about a code."""
    return (
        f'This workspace records every amount in NZD, so {code} cannot be '
        'entered. Turn on multiple currencies in workspace settings to record '
        'another.'
    )


#: The euro is the currency almost every test below tries, so it has a name.
REFUSAL = refusal_for('EUR')


class MultiCurrencyDefaultTests(RESTContractTestCase):
    """Verification 1: nobody is asked for a currency until they ask to be."""

    url = '/settings/workspace/'

    def test_a_workspace_enters_one_currency_until_it_is_told_otherwise(self):
        """The field defaults off, and the migration backfilled nothing."""
        self.assertFalse(get_current_workspace().multi_currency_enabled)
        self.assertFalse(Workspace(name='Second bench').multi_currency_enabled)

    def test_the_profile_publishes_the_switch_and_takes_it_back(self):
        """The screen beside the currency code is where it is turned on."""
        self.assertFalse(self.client.get(self.url).data['multi_currency_enabled'])

        response = self.client.patch(
            self.url, {'multi_currency_enabled': True}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(get_current_workspace().multi_currency_enabled)

    def test_the_rule_itself_asks_the_workspace_and_nothing_else(self):
        """Case is not part of the comparison; the switch and the code are."""
        workspace = get_current_workspace()
        workspace.currency_code = 'NZD'
        self.assertIsNone(currency_input_refusal(workspace, 'nzd'))
        self.assertIsNone(currency_input_refusal(workspace, None))
        self.assertEqual(currency_input_refusal(workspace, 'eur'), REFUSAL)
        workspace.multi_currency_enabled = True
        self.assertIsNone(currency_input_refusal(workspace, 'EUR'))


class CurrencyInputTestCase(RESTContractTestCase):
    """One NZD nursery and the ten documents a currency is entered on."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.supplier = make_supplier(workspace=self.workspace)
        self.location = make_location()
        self.item = make_inventory_item(base_unit=UnitCode.LITRE)
        self.category = make_expense_category()
        self.income_year = IncomeTaxYear.objects.create(
            workspace=self.workspace, year_end=date(2027, 3, 31), basis='accrual',
        )

    def receipt_payload(self, **overrides):
        """Receive two litres of media from the supplier."""
        return {
            'supplier': self.supplier.pk,
            'received_date': '2026-08-01',
            'supplier_reference': 'DEL-159',
            'lines': [{
                'item': self.item.pk,
                'quantity': '2.000000000',
                'unit_code': UnitCode.LITRE,
                'line_cost_ex_tax': '10.0000',
                'supplier_cost_incl_tax': '11.5000',
                'tax_treatment': 'standard',
                'tax_rate': '15.0000',
                'input_tax_source': 'supplier',
                'input_tax_amount': '1.5000',
                'claim_input_tax': False,
                'claimable_percentage': '0.0000',
                'destination': self.location.pk,
            }],
            **overrides,
        }

    def order_payload(self, **overrides):
        """Order the same media rather than receiving it."""
        return {
            'order_number': f"PO-{overrides.pop('number', '159')}",
            'supplier': self.supplier.pk,
            'ordered_on': '2026-08-15',
            'lines': [{
                'item': self.item.pk,
                'description': 'Growing media',
                'quantity': '100.000000000',
                'unit_code': UnitCode.LITRE,
                'unit_price_ex_tax': '0.1000',
                'tax_rate': '15.0000',
                'freight_ex_tax': '2.0000',
            }],
            **overrides,
        }

    def invoice_payload(self, **overrides):
        """Take the supplier's bill for it."""
        return {
            'supplier': self.supplier.pk,
            'external_reference': f"INV-{overrides.pop('number', '159')}",
            'invoice_date': '2026-08-21',
            'due_date': '2026-09-20',
            'lines': [{
                'description': 'Growing media',
                'is_freight': False,
                'subtotal_ex_tax': '10.0000',
                'tax_rate': '15.0000',
                'tax_total': '1.5000',
                'total_incl_tax': '11.5000',
            }],
            **overrides,
        }

    def payment_payload(self, **overrides):
        """Pay the supplier."""
        return {
            'supplier': self.supplier.pk,
            'paid_on': '2026-08-25',
            'amount': '11.5000',
            'method': 'bank_transfer',
            'external_reference': f"PAY-{overrides.pop('number', '159')}",
            **overrides,
        }

    def expense_payload(self, **overrides):
        """Record a cost that brings no stock in."""
        return {
            'category': self.category.pk,
            'payee': 'Saturday market',
            'incurred_on': '2026-08-22',
            'subtotal_ex_tax': '20.0000',
            'tax_total': '3.0000',
            'total_incl_tax': '23.0000',
            **overrides,
        }

    def sales_order_payload(self, **overrides):
        """Open a counter order to sell from."""
        return {'status': SalesOrder.Status.DRAFT, 'notes': 'Counter order', **overrides}

    def liability_payload(self, **overrides):
        """Name the loan the tunnel house was built on."""
        return {
            'code': f"LOAN-{overrides.pop('number', '159')}",
            'name': 'Tunnel house loan',
            'counterparty': 'Rural Bank',
            'opened_on': '2026-04-01',
            **overrides,
        }

    def bookkeeping_entry_payload(self, **overrides):
        """Record money that arrived through neither sales nor purchasing."""
        return {
            'kind': 'other_income',
            'occurred_on': '2026-06-01',
            'description': f"Propagation workshop {overrides.pop('number', '159')}",
            'amount_ex_tax': '100.0000',
            'tax_amount': '15.0000',
            'total_incl_tax': '115.0000',
            'tax_treatment': 'standard',
            **overrides,
        }

    def tax_asset_payload(self, **overrides):
        """Put the tiller in the register the return depreciates it from."""
        return {
            'code': f"TILLER-{overrides.pop('number', '159')}",
            'name': 'Tiller',
            'category': 'Machinery',
            'acquired_on': '2026-04-01',
            'cost_incl_tax': '1150.0000',
            'recoverable_tax': '150.0000',
            'tax_cost': '1000.0000',
            **overrides,
        }

    def stock_line_payload(self, **overrides):
        """Price closing stock by hand, which is where an operator types one.

        The task filed this with the snapshots that copy a currency from the
        record above them. It is not one: `add_stock_line` takes a whole
        serializer from the request body, and the operator valuing stock
        names the value, the method *and* the currency.
        """
        return {
            'income_year': self.income_year.pk,
            'category': 'other',
            'description': 'Packed produce',
            'source_type': 'manual',
            'source_id': f"PACKED-{overrides.pop('number', '159')}",
            'original_cost': '50.0000',
            'method': 'cost',
            'value': '50.0000',
            'evidence_url': 'https://example.test/stocktake.pdf',
            **overrides,
        }

    def entry_points(self, **overrides):
        """Return one create request per document a currency is entered on."""
        return (
            ('receipt', '/inventory/receipts/', self.receipt_payload(**overrides)),
            ('purchase order', '/purchasing/orders/', self.order_payload(**overrides)),
            ('supplier invoice', '/purchasing/invoices/', self.invoice_payload(**overrides)),
            ('supplier payment', '/purchasing/payments/', self.payment_payload(**overrides)),
            ('business expense', '/purchasing/expenses/', self.expense_payload(**overrides)),
            ('sales order', '/sales/orders/', self.sales_order_payload(**overrides)),
            ('liability', '/bookkeeping/liabilities/', self.liability_payload(**overrides)),
            ('bookkeeping entry', '/bookkeeping/entries/', self.bookkeeping_entry_payload(**overrides)),
            ('tax asset', '/bookkeeping/assets/', self.tax_asset_payload(**overrides)),
            (
                'stock valuation line',
                f'/bookkeeping/income-years/{self.income_year.pk}/stock-lines/',
                self.stock_line_payload(**overrides),
            ),
        )

    def assert_nothing_was_written(self):
        """A refused currency leaves no document of any kind behind."""
        for model in (
            StockReceipt, PurchaseOrder, SupplierInvoice, SupplierPayment,
            BusinessExpense, SalesOrder, Liability, BookkeepingEntry, TaxAsset,
            StockValuationLine,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)


class SingleCurrencyInputTests(CurrencyInputTestCase):
    """Verifications 2 and 3: the workspace's own currency, and no other."""

    def test_the_workspaces_own_currency_is_accepted_where_it_is_named(self):
        """A client that fills the field in correctly has done nothing wrong."""
        for name, url, payload in self.entry_points(currency_code='NZD'):
            with self.subTest(document=name):
                response = self.client.post(url, payload, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['currency_code'], 'NZD')

    def test_a_document_that_names_no_currency_is_filed_in_the_workspaces(self):
        """Receiving and sales already defaulted, and the default is the one."""
        receipt = self.client.post(
            '/inventory/receipts/', self.receipt_payload(), format='json',
        )
        self.assertEqual(receipt.status_code, 201, receipt.data)
        self.assertEqual(receipt.data['currency_code'], 'NZD')

        order = self.client.post(
            '/sales/orders/', self.sales_order_payload(), format='json',
        )
        self.assertEqual(order.status_code, 201, order.data)
        self.assertEqual(order.data['currency_code'], 'NZD')

    def test_a_currency_typed_in_lower_case_is_stored_in_upper(self):
        """What is stored, not only what is accepted.

        Four of the columns state the shape of a code themselves and turn
        `nzd` away before the rule is reached. The rest are a bare
        three-character column, and a row reading `nzd` would look foreign to
        every reader that compares the code -- a two-currency mixture invented
        out of a shift key. So what is accepted is normalized, not echoed.
        """
        payment = self.client.post(
            '/purchasing/payments/', self.payment_payload(currency_code='nzd'),
            format='json',
        )
        self.assertEqual(payment.status_code, 201, payment.data)
        self.assertEqual(payment.data['currency_code'], 'NZD')
        self.assertEqual(
            SupplierPayment.objects.get(pk=payment.data['pk']).currency_code, 'NZD',
        )

        expense = self.client.post(
            '/purchasing/expenses/', self.expense_payload(currency_code='nzd'),
            format='json',
        )
        self.assertEqual(expense.status_code, 201, expense.data)
        self.assertEqual(expense.data['currency_code'], 'NZD')
        self.assertEqual(
            BusinessExpense.objects.get(pk=expense.data['pk']).currency_code, 'NZD',
        )

    def test_another_currency_is_refused_by_name_and_nothing_is_written(self):
        """Not ignored, and not overwritten: said out loud and turned away."""
        for name, url, payload in self.entry_points(currency_code='EUR'):
            with self.subTest(document=name):
                response = self.client.post(url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(
                    [str(message) for message in response.data['currency_code']],
                    [REFUSAL],
                )
        self.assert_nothing_was_written()

    def test_converting_a_requisition_refuses_the_same_currency(self):
        """The one currency entered through an action, not a document."""
        requisition = self.client.post('/purchasing/requisitions/', {
            'item': self.item.pk,
            'required_on': '2026-09-10',
            'quantity': '100.000000000',
            'unit_code': UnitCode.LITRE,
            'preferred_supplier': self.supplier.pk,
            'estimated_total_incl_tax': '11.5000',
            'notes': 'The benches are out of media.',
        }, format='json')
        self.assertEqual(requisition.status_code, 201, requisition.data)
        reviewed = self.client.post(
            f"/purchasing/requisitions/{requisition.data['pk']}/review/", {},
            format='json',
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)

        response = self.client.post(
            f"/purchasing/requisitions/{requisition.data['pk']}/order/", {
                'order_number': 'PO-REQ-159',
                'supplier': self.supplier.pk,
                'ordered_on': '2026-08-15',
                'currency_code': 'EUR',
                'unit_price_ex_tax': '0.1000',
                'tax_rate': '15.0000',
            }, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            [str(message) for message in response.data['currency_code']], [REFUSAL],
        )


class BookkeepingCurrencyInputTests(CurrencyInputTestCase):
    """The tax working papers ask for a currency too.

    The task filed all four of them with the snapshots, on the grounds that
    the browser sends the workspace's code and there is no input to hide. The
    valuation line is the counter-example, and the other three are
    `fields = '__all__'` on a writable viewset, which is an input whatever the
    browser happens to send.
    """

    def test_a_foreign_valuation_line_never_reaches_the_income_year(self):
        """Where the refusal has to be, given what one euro line costs.

        `ForeignCurrencyStockTests` (`bookkeeping.test_mixed_currency`) pins
        the other end: a line in another currency raises
        `unsupported_currency`, and that finding refuses to finalize the year
        at all. A line nobody can enter is a year nobody has to unpick.
        """
        response = self.client.post(
            f'/bookkeeping/income-years/{self.income_year.pk}/stock-lines/',
            self.stock_line_payload(currency_code='EUR'), format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            [str(message) for message in response.data['currency_code']], [REFUSAL],
        )
        self.assertEqual(self.income_year.stock_lines.count(), 0)

    def test_a_currency_typed_in_lower_case_is_stored_in_upper_here_too(self):
        """None of the four columns states the shape of a code itself."""
        entry = self.client.post(
            '/bookkeeping/entries/', self.bookkeeping_entry_payload(currency_code='nzd'),
            format='json',
        )

        self.assertEqual(entry.status_code, 201, entry.data)
        self.assertEqual(
            BookkeepingEntry.objects.get(pk=entry.data['id']).currency_code, 'NZD',
        )


class MultiCurrencyInputTests(CurrencyInputTestCase):
    """Verification 4: with the switch on, every one of them is unchanged."""

    def setUp(self):
        super().setUp()
        self.workspace.multi_currency_enabled = True
        self.workspace.save()

    def test_every_document_takes_the_currency_it_is_given(self):
        """This is what the application did before the switch existed."""
        for name, url, payload in self.entry_points(currency_code='EUR'):
            with self.subTest(document=name):
                response = self.client.post(url, payload, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['currency_code'], 'EUR')


class ForeignRecordsSurviveTheSwitchTests(CurrencyInputTestCase):
    """A document received abroad keeps its currency and still shows it."""

    def setUp(self):
        super().setUp()
        self.workspace.multi_currency_enabled = True
        self.workspace.save()
        created = self.client.post(
            '/inventory/receipts/', self.receipt_payload(currency_code='EUR'),
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.receipt = created.data
        self.workspace.multi_currency_enabled = False
        self.workspace.save()

    def test_the_draft_still_says_what_it_was_entered_in(self):
        """Turning the switch off hides the question, not the answer."""
        response = self.client.get(f"/inventory/receipts/{self.receipt['pk']}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency_code'], 'EUR')

    def test_an_unrelated_edit_leaves_the_currency_where_it_is(self):
        """What the receiving form sends: everything except the currency."""
        response = self.client.patch(
            f"/inventory/receipts/{self.receipt['pk']}/",
            {'notes': 'Checked against the packing list.'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency_code'], 'EUR')
        self.assertEqual(
            StockReceipt.objects.get(pk=self.receipt['pk']).currency_code, 'EUR',
        )

    def test_sending_the_stored_currency_back_saves_the_draft_unchanged(self):
        """An echo is the draft repeating itself, and changes no recorded money."""
        response = self.client.patch(
            f"/inventory/receipts/{self.receipt['pk']}/",
            {'currency_code': 'EUR'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency_code'], 'EUR')
        self.assertEqual(
            StockReceipt.objects.get(pk=self.receipt['pk']).currency_code, 'EUR',
        )

    def test_a_currency_the_draft_is_not_in_is_still_refused(self):
        """The draft's own code, and nothing else: the switch is still off."""
        response = self.client.patch(
            f"/inventory/receipts/{self.receipt['pk']}/",
            {'currency_code': 'USD'}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            [str(message) for message in response.data['currency_code']],
            [refusal_for('USD')],
        )
        self.assertEqual(
            StockReceipt.objects.get(pk=self.receipt['pk']).currency_code, 'EUR',
        )


class ForeignPurchasingDraftsStayEditableTests(CurrencyInputTestCase):
    """The two documents that can only be updated whole.

    `PurchaseOrderViewSet` and `SupplierInvoiceViewSet` offer PUT and no
    PATCH, and both write serializers require `currency_code`, so every edit
    to a euro draft names a currency. While an echo was refused, the only
    update either of them accepted was one that refiled recorded money in the
    workspace's own currency -- which is the outcome the decisions call out by
    name.
    """

    def setUp(self):
        super().setUp()
        self.workspace.multi_currency_enabled = True
        self.workspace.save()
        self.order = self.created('/purchasing/orders/', self.order_payload(currency_code='EUR'))
        self.invoice = self.created('/purchasing/invoices/', self.invoice_payload(currency_code='EUR'))
        self.workspace.multi_currency_enabled = False
        self.workspace.save()

    def created(self, url, payload):
        """Post one document while the switch is on and return what came back."""
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_a_euro_order_is_saved_again_in_the_currency_it_carries(self):
        """The whole document, including the currency it has always had."""
        response = self.client.put(
            f"/purchasing/orders/{self.order['pk']}/",
            self.order_payload(currency_code='EUR', notes='Confirmed by phone'),
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency_code'], 'EUR')
        self.assertEqual(
            PurchaseOrder.objects.get(pk=self.order['pk']).currency_code, 'EUR',
        )

    def test_a_euro_invoice_is_saved_again_the_same_way(self):
        """The supplier's bill for it, edited the one way the route allows."""
        response = self.client.put(
            f"/purchasing/invoices/{self.invoice['pk']}/",
            self.invoice_payload(currency_code='EUR', due_date='2026-10-20'),
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['currency_code'], 'EUR')
        self.assertEqual(
            SupplierInvoice.objects.get(pk=self.invoice['pk']).currency_code, 'EUR',
        )

    def test_the_update_still_refuses_a_currency_the_order_is_not_in(self):
        """An echo is one answer, not an open door."""
        response = self.client.put(
            f"/purchasing/orders/{self.order['pk']}/",
            self.order_payload(currency_code='USD'), format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            [str(message) for message in response.data['currency_code']],
            [refusal_for('USD')],
        )

    def test_a_new_document_cannot_borrow_a_stored_currency(self):
        """The create path is untouched: there is no instance to echo."""
        response = self.client.post(
            '/purchasing/orders/', self.order_payload(number='160', currency_code='EUR'),
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            [str(message) for message in response.data['currency_code']], [REFUSAL],
        )


class MixedCostsStillRefuseWithTheSwitchOffTests(MixedCurrencyTestCase):
    """Verification 5: task 142 does not depend on the switch being on.

    The batch is the one 142 measured -- a USD seed lot, a USD media
    application and a EUR one -- in a workspace that has never turned multiple
    currencies on, which is now every workspace by default. The stock is
    there, so the refusal has to be too.
    """

    def setUp(self):
        super().setUp()
        self.plant = self.germinated_plant()

    def test_the_workspace_holding_the_mixture_has_the_switch_off(self):
        """The fixture is the state the migration leaves every workspace in."""
        self.assertFalse(self.workspace.multi_currency_enabled)

    def test_the_batch_still_states_both_totals_and_no_combined_figure(self):
        """1.08 USD and 0.08 EUR, and still never 1.1600."""
        breakdown = batch_cost_breakdown(self.batch)
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['provisional_total'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in breakdown['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )
        self.assertNotIn('1.1600', repr(breakdown))

    def test_the_foreign_lot_still_carries_the_currency_it_was_bought_in(self):
        """Nothing relabelled the euro media as dollars when the switch went off."""
        self.assertEqual(self.euro_media.currency_code, 'EUR')
        self.assertEqual(
            sorted((row.currency_code, f'{row.amount:.4f}') for row in self.effective()),
            [('EUR', '0.0800'), ('USD', '0.0800'), ('USD', '1.0000')],
        )

    def test_the_plant_raised_on_it_still_states_no_value(self):
        """The refusal reaches the plant, which is where a sale reads it."""
        breakdown = plant_cost_breakdown(self.plant)
        self.assertTrue(breakdown['mixed_currency'])
        self.assertIsNone(breakdown['currency_code'])
        self.assertIsNone(breakdown['provisional_value'])
