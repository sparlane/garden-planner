"""Purchases and adjustments converted into the workspace's currency (task 121).

The purchase side of the change list: a posted stock receipt bought in euros, a
later input-tax adjustment against one of its lines, and the confirmed supplier
invoice beside them. Each carries its own rate, because each was a transaction
on its own day -- an adjustment made in April is not converted at the rate the
receipt was landed at in August.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring,duplicate-code

from datetime import date
from decimal import Decimal

from inventory.models import InputTaxAdjustment, StockReceipt, StockReceiptLine
from inventory.test_ledger_rest import LedgerRestFixture
from workspaces.models import Workspace

from .conversion import live_conversion
from .models import CurrencyConversion


#: The rate every euro purchase below is converted at. See
#: `test_currency_conversion` for what it is and why it is this one.
EURO_RATE = '1.6701057193'


class PurchaseConversionTestCase(LedgerRestFixture):
    """One euro receipt, posted, in a workspace that records dollars."""

    url = '/bookkeeping/currency-conversions/'

    def setUp(self):
        super().setUp()
        Workspace.objects.filter(pk=self.workspace.pk).update(
            multi_currency_enabled=True,
        )
        self.workspace.refresh_from_db()

    def post_euro_receipt(self):
        """Post a receipt while the workspace recorded euros, then move to dollars.

        `post_receipt` refuses a receipt in any currency but the workspace's,
        so a posted receipt becomes foreign the way task 121's first decision
        describes: the workspace's own currency changed afterwards and the rows
        recorded before it kept what they were bought in. That is also the
        shape of every workspace left on the default code that later sets its
        own.
        """
        self.record_in('EUR')
        created = self.client.post(
            self.receipt_url, self.receipt_payload(), format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        posted = self.client.post(
            f"{self.receipt_url}{created.data['pk']}/post/", {}, format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.record_in('NZD')
        return StockReceipt.objects.get(pk=created.data['pk'])

    def record_in(self, currency_code):
        """Switch the currency this workspace records its amounts in."""
        Workspace.objects.filter(pk=self.workspace.pk).update(
            currency_code=currency_code,
        )
        self.workspace.refresh_from_db()

    def convert(self, source_type, source_id, **overrides):
        """Type a rate against one transaction, the way a screen does."""
        payload = {
            'source_type': source_type,
            'source_id': str(source_id),
            'rate': EURO_RATE,
            'quote_direction': 'target_per_source',
            'rate_source': 'Reserve Bank of New Zealand',
            'effective_date': '2026-08-01',
        }
        payload.update(overrides)
        return self.client.post(self.url, payload, format='json')

    def converted(self, response):
        """Return the converted figures by name."""
        return {row['name']: row['converted'] for row in response.data['amounts']}

    def listed(self, **params):
        """Return the conversions a list request answers with."""
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        records = response.data
        return records['results'] if isinstance(records, dict) else records


class ReceiptConversionTests(PurchaseConversionTestCase):
    """Verification 2: a foreign purchase reconciles to a stated figure."""

    def test_a_posted_euro_receipt_converts_every_figure_it_carries(self):
        receipt = self.post_euro_receipt()

        response = self.convert('stock_receipt', receipt.pk)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.converted(response), {
            'lines_ex_tax': '16.7011',
            'recoverable_input_tax': '0.0000',
            'non_recoverable_tax': '2.5052',
            'acquisition_amount': '19.2062',
        })
        self.assertEqual(response.data['source_currency_code'], 'EUR')
        self.assertEqual(response.data['target_currency_code'], 'NZD')

    def test_the_receipt_itself_still_says_what_was_paid_in_euros(self):
        receipt = self.post_euro_receipt()

        self.convert('stock_receipt', receipt.pk)

        receipt.refresh_from_db()
        self.assertEqual(receipt.currency_code, 'EUR')
        line = receipt.lines.get()
        self.assertEqual(f'{line.line_cost_ex_tax:.4f}', '10.0000')
        self.assertEqual(f'{line.acquisition_amount:.4f}', '11.5000')

    def test_a_receipt_still_in_draft_carries_no_rate(self):
        self.record_in('EUR')
        created = self.client.post(
            self.receipt_url, self.receipt_payload(), format='json',
        )
        self.record_in('NZD')

        response = self.convert('stock_receipt', created.data['pk'])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data['source_id'],
            ['Only a posted stock receipt can be converted.'],
        )

    def test_a_receipt_in_the_workspace_currency_is_not_converted_twice(self):
        """The backfill's identity conversion is the rate such a row reads at."""
        self.create_and_post_receipt()
        receipt = StockReceipt.objects.filter(currency_code='NZD').first()

        response = self.convert('stock_receipt', receipt.pk)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            'is already in NZD, so there is nothing to convert',
            response.data['source_currency_code'][0],
        )


class AdjustmentConversionTests(PurchaseConversionTestCase):
    """An adjustment is its own transaction, on its own day, at its own rate."""

    def setUp(self):
        super().setUp()
        self.receipt = self.post_euro_receipt()
        self.line = StockReceiptLine.objects.get(receipt=self.receipt)
        self.adjustment = InputTaxAdjustment.objects.create(
            workspace=self.workspace,
            receipt_line=self.line,
            adjustment_date=date(2026, 10, 1),
            previous_claimable_percentage=Decimal('0'),
            revised_claimable_percentage=Decimal('40'),
            tax_adjustment=Decimal('0.6'),
            apportionment_basis='Observed taxable use',
            reason='Taxable use rose after the season started',
            created_by=self.user,
        )

    def test_an_adjustment_takes_the_currency_of_the_receipt_it_adjusts(self):
        response = self.convert('input_tax_adjustment', self.adjustment.pk)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['source_currency_code'], 'EUR')
        self.assertEqual(self.converted(response), {'tax_adjustment': '1.0021'})

    def test_it_carries_its_own_rate_rather_than_the_receipt_s(self):
        self.convert('stock_receipt', self.receipt.pk)

        response = self.convert(
            'input_tax_adjustment', self.adjustment.pk,
            rate='2.0000000000', effective_date='2026-10-01',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.converted(response), {'tax_adjustment': '1.2000'})
        receipt_rate = live_conversion(
            self.workspace, 'stock_receipt', self.receipt.pk,
        )
        self.assertEqual(f'{receipt_rate.rate:f}', EURO_RATE)
        self.assertEqual(receipt_rate.effective_date, date(2026, 8, 1))


class ConversionRouteTests(PurchaseConversionTestCase):
    """The route an operator types a rate through, and reads them back from."""

    def test_the_route_refuses_a_method_the_workspace_does_not_convert_by(self):
        receipt = self.post_euro_receipt()

        response = self.convert(
            'stock_receipt', receipt.pk, method='period_end',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            'This workspace converts every foreign amount at the spot rate',
            response.data['method'][0],
        )

    def test_the_route_refuses_a_record_no_rate_is_recorded_against(self):
        response = self.convert('cost_layer', 1)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('source_type', response.data)

    def test_the_rates_of_one_record_are_listed_newest_state_first(self):
        receipt = self.post_euro_receipt()
        first = self.convert('stock_receipt', receipt.pk)
        corrected = self.convert(
            'stock_receipt', receipt.pk, rate='2.0000000000',
            supersedes=first.data['id'],
        )
        self.assertEqual(corrected.status_code, 201, corrected.data)

        every = self.listed(source_type='stock_receipt', source_id=receipt.pk)
        live = self.listed(
            source_type='stock_receipt', source_id=receipt.pk, live='true',
        )

        self.assertEqual(
            {row['id'] for row in every},
            {first.data['id'], corrected.data['id']},
        )
        self.assertEqual([row['id'] for row in live], [corrected.data['id']])

    def test_a_recorded_rate_cannot_be_edited_or_deleted_through_the_route(self):
        receipt = self.post_euro_receipt()
        created = self.convert('stock_receipt', receipt.pk)
        detail = f"{self.url}{created.data['id']}/"

        self.assertEqual(
            self.client.patch(detail, {'rate': '9'}, format='json').status_code, 405,
        )
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.assertEqual(CurrencyConversion.objects.count(), 1)
