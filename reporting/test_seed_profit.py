"""Per-seed-lot profit: realized, committed on open orders, and still pending."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from decimal import Decimal

from costing.test_services import CostingServiceTestCase
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from tests.factories import reserve_plants
from workspaces.models import Workspace

from .test_commerce import CommerceReportTestCase


class SeedProfitLotTests(CostingServiceTestCase):
    """One plant raised from a costed packet, first unsold and then promised."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        sowing = self.sow([(self.cells[0], 4)])
        self.plant = self.germinate(sowing, self.cells[0])[0]
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        self.reallocate()

    def report(self, **params):
        response = self.client.get('/reports/seed-profit/', params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def lot_row(self, body):
        rows = [r for r in body['results'] if r['lot_id'] == self.packet.stock_lot_id]
        self.assertEqual(len(rows), 1, body['results'])
        return rows[0]

    def test_the_sown_lot_has_a_row_with_nothing_sold_or_promised(self):
        row = self.lot_row(self.report())

        self.assertEqual(Decimal(row['net_sales']), 0)
        self.assertEqual(Decimal(row['committed_units']), 0)
        self.assertGreaterEqual(Decimal(row['pending_units']), 1)

    def test_a_reservation_moves_a_unit_from_pending_to_committed(self):
        before = Decimal(self.lot_row(self.report())['pending_units'])
        reserve_plants(self.workspace, self.user, [self.plant])

        row = self.lot_row(self.report())

        self.assertEqual(Decimal(row['committed_units']), 1)
        self.assertEqual(Decimal(row['committed_revenue']), Decimal('10'))
        self.assertEqual(Decimal(row['pending_units']), before - 1)

    def test_the_variety_filter_leaves_other_varieties_out(self):
        body = self.report(variety=self.plant.batch.variety_id + 1000)

        self.assertEqual(body['results'], [])


class SeedProfitRealizedTests(CommerceReportTestCase):
    """A dispatched sale is realized profit, under the row for no recorded lot."""

    def test_realized_profit_is_net_sales_less_cost(self):
        response = self.client.get('/reports/seed-profit/')
        self.assertEqual(response.status_code, 200, response.data)

        row = response.data['results'][0]
        self.assertIsNone(row['lot_id'])
        self.assertEqual(Decimal(row['net_sales']), Decimal('9'))
        self.assertEqual(Decimal(row['direct_cogs']), Decimal('2'))
        self.assertEqual(Decimal(row['realized_profit']), Decimal('7'))

    def test_a_provisional_cost_is_counted_and_flagged_not_dropped(self):
        # Nothing finalizes a batch until its output is done, so a real garden
        # has only provisional costs for a long time. They must still show.
        self.fulfillment_line.cogs_provisional = True
        self.fulfillment_line.save(update_fields=['cogs_provisional'])

        response = self.client.get('/reports/seed-profit/')

        row = response.data['results'][0]
        self.assertEqual(Decimal(row['net_sales']), Decimal('9'))
        self.assertEqual(Decimal(row['realized_profit']), Decimal('7'))
        self.assertTrue(row['provisional'])
        self.assertTrue(row['complete'])
        codes = [item['code'] for item in response.data['data_quality']]
        self.assertIn('provisional_cost', codes)

    def test_the_export_carries_the_same_rows(self):
        response = self.client.get('/reports/seed-profit/export/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('realized_profit', response.content.decode())
