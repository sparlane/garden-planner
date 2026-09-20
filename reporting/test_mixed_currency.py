"""The reports publish the refusal rather than a consolidated figure (142).

Both of these read `batch_cost_breakdown` or `plant_cost_breakdown`, so what
they publish is whatever those state. The production report divided the mixed
total into a unit cost as well, and the plant trace stated it as a value under
one currency code. The wording of the finding matches
`reporting.commerce.profitability_report`, which has declined to consolidate
currencies all along.
"""

# pylint: disable=duplicate-code

from costing.test_currencies import MixedCurrencyTestCase
from workspaces.models import Workspace


NOT_CONSOLIDATED = 'No exchange rate exists, so currencies are not consolidated.'


class MixedCurrencyReportTestCase(MixedCurrencyTestCase):
    """The same mixed batch, with one seedling up, read through the reports."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save(update_fields=['mode'])
        self.plant = self.germinated_plant()

    def report(self, path, **params):
        """Return one report body from the endpoint the screens call."""
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def findings(self, body):
        """Return the body's data-quality entries by code."""
        return {row['code']: row for row in body['data_quality']}


class ProductionReportTests(MixedCurrencyReportTestCase):  # pylint: disable=too-many-ancestors
    """A batch with no stateable total has no unit cost to divide out of it."""

    def body(self):
        """Return the production report narrowed to the fixture's batch."""
        return self.report('/reports/production-batches/', batch=self.batch.pk)

    def test_the_row_states_no_total_and_no_unit_cost(self):
        """Dividing 1.16 by the output would price a seedling in neither."""
        row = self.body()['results'][0]
        self.assertTrue(row['mixed_currency'])
        self.assertIsNone(row['currency_code'])
        self.assertIsNone(row['provisional_total'])
        self.assertIsNone(row['final_total'])
        self.assertIsNone(row['unit_cost'])
        self.assertIsNone(row['plant_inventory_value'])

    def test_the_row_lists_what_the_batch_cost_in_each_currency(self):
        """Refusing one figure still has to report the two that exist."""
        row = self.body()['results'][0]
        self.assertEqual(
            [(entry['currency_code'], entry['amount']) for entry in row['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )

    def test_the_report_names_the_mixture_as_a_data_quality_finding(self):
        """Verification 6: a reader is told why the total is missing."""
        body = self.body()
        finding = self.findings(body)['mixed_currency']
        # Currencies, as the profitability report counts them; batches are the
        # totals figure beside it.
        self.assertEqual(finding['count'], 2)
        self.assertEqual(finding['message'], NOT_CONSOLIDATED)
        self.assertEqual(body['totals']['mixed_currency_batches'], 1)


class PlantTraceTests(MixedCurrencyReportTestCase):  # pylint: disable=too-many-ancestors
    """The backward trace states the currencies, never a value across them."""

    def body(self):
        """Return the backward trace for the fixture's one seedling."""
        return self.report(f'/reports/traceability/plants/{self.plant.pk}/')

    def test_the_trace_states_no_value_and_lists_both_currencies(self):
        """Each layer keeps its own currency; the total is not one of them."""
        totals = self.body()['totals']
        self.assertTrue(totals['mixed_currency'])
        self.assertIsNone(totals['provisional_value'])
        self.assertIsNone(totals['final_value'])
        self.assertIsNone(totals['currency_code'])
        self.assertEqual(
            [(row['currency_code'], row['amount']) for row in totals['currencies']],
            [('EUR', '0.0800'), ('USD', '1.0800')],
        )

    def test_the_trace_names_the_mixture_as_a_data_quality_finding(self):
        """Told apart from an unvalued input, which this plant does not have."""
        findings = self.findings(self.body())
        self.assertEqual(findings['mixed_currency']['message'], NOT_CONSOLIDATED)
        self.assertEqual(findings['mixed_currency']['count'], 2)
        self.assertNotIn('unvalued_input', findings)
