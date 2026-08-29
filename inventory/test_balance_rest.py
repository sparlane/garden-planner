"""Contract tests for the derived balance projection and its query filters."""

from datetime import date, timedelta
from decimal import Decimal

from .ledger import MovementRequest, post_stock_movement
from .models import StockLot, StockMovement
from .test_ledger_rest import LedgerRestFixture
from .units import UnitCode


class BalanceProjectionTests(LedgerRestFixture):
    """Balance rows follow lot history and never value more than they know."""

    def rows(self, **query):
        """Return the balance rows one filtered request produces."""
        response = self.client.get(self.balance_url, query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def rows_by_location(self, lot_pk, **query):
        """Return one lot's rows keyed by the location each one describes."""
        return {
            row['location']: row
            for row in self.rows(**query)
            if row['lot'] == lot_pk
        }

    def transfer(self, lot_pk, quantity, source, destination):
        """Move part of a lot between two locations."""
        response = self.client.post(
            f'{self.movement_url}transfer/',
            {
                'lot': lot_pk,
                'quantity': quantity,
                'unit_code': UnitCode.MILLILITRE,
                'source': source.pk,
                'destination': destination.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def consume(self, lot_pk, quantity, source):
        """Consume part of a lot out of one location."""
        response = self.client.post(
            f'{self.movement_url}consume/',
            {
                'lot': lot_pk,
                'quantity': quantity,
                'unit_code': UnitCode.MILLILITRE,
                'source': source.pk,
                'reason': 'Potting run',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_a_lot_keeps_a_row_at_every_location_its_history_touched(self):
        """Stock that has left a place is reported as zero, not omitted."""
        lot = self.create_opening(quantity='500.000000000')['lot']['pk']
        self.transfer(lot, '200.000000000', self.store, self.growing)

        split = self.rows_by_location(lot)
        self.assertEqual(
            {location: row['physical_quantity'] for location, row in split.items()},
            {
                self.store.pk: '300.000000000',
                self.growing.pk: '200.000000000',
            },
        )

        self.transfer(lot, '300.000000000', self.store, self.growing)

        emptied = self.rows_by_location(lot)
        self.assertEqual(
            {location: row['physical_quantity'] for location, row in emptied.items()},
            {
                self.store.pk: '0.000000000',
                self.growing.pk: '500.000000000',
            },
        )
        self.assertEqual(emptied[self.store.pk]['valuation'], '0.0000')
        self.assertEqual(
            sum(Decimal(row['physical_quantity']) for row in emptied.values()),
            Decimal('500.000000000'),
        )

    def test_low_stock_answers_for_the_item_whatever_the_filter_shows(self):
        """Narrowing the rows must not change what the item is judged against.

        The threshold is the item's, so a filtered view is a window onto one
        answer rather than a question of its own. A row that flipped to low
        because the request happened to hide the rest of the item's stock would
        send someone to reorder against a shortage that is not there.
        """
        expiring = (date.today() + timedelta(days=10)).isoformat()
        near = self.create_opening(
            quantity='200.000000000',
            expires_on=expiring,
        )['lot']['pk']
        self.create_opening(quantity='100.000000000', destination=self.growing.pk)
        views = {
            'unfiltered': {},
            'one lot': {'lot': near},
            'one location': {'location': self.store.pk},
            'expiring soon': {'expires_before': expiring},
        }

        for label, query in views.items():
            with self.subTest(view=label, total='300 against a threshold of 250'):
                rows = self.rows(**query)
                self.assertTrue(rows)
                self.assertEqual([row['low_stock'] for row in rows], [False] * len(rows))

        self.consume(near, '60.000000000', self.store)

        for label, query in views.items():
            with self.subTest(view=label, total='240 against a threshold of 250'):
                rows = self.rows(**query)
                self.assertTrue(rows)
                self.assertEqual([row['low_stock'] for row in rows], [True] * len(rows))

    def test_an_item_with_no_threshold_is_never_low(self):
        """Low stock is a comparison, and there is nothing to compare against."""
        self.item.reorder_level = None
        self.item.save(update_fields=['reorder_level'])
        self.create_opening(quantity='1.000000000')

        self.assertEqual(
            [row['low_stock'] for row in self.rows()],
            [False],
        )
        self.assertEqual(self.rows(low_stock='true'), [])

    def test_valuation_rounds_each_row_and_still_sums_to_what_was_paid(self):
        """A cost that does not divide evenly is not allowed to leak value."""
        lot = self.create_opening(
            quantity='3.000000000',
            acquisition_total='1.0000',
        )['lot']['pk']
        self.transfer(lot, '2.000000000', self.store, self.growing)

        split = self.rows_by_location(lot)
        self.assertEqual(
            split[self.store.pk]['base_unit_cost'],
            '0.333333333333',
        )
        # 1 x 0.333333333333 rounds down and 2 x 0.333333333333 rounds up, so
        # the two rows land on either side of the exact third.
        self.assertEqual(split[self.store.pk]['valuation'], '0.3333')
        self.assertEqual(split[self.growing.pk]['valuation'], '0.6667')
        self.assertEqual(
            sum(Decimal(row['valuation']) for row in split.values()),
            Decimal('1.0000'),
        )

    def unknown_lot(self):
        """Receive one lot whose contents nobody has counted yet."""
        draft = self.client.post(
            self.receipt_url,
            self.receipt_payload(lines=[{
                **self.receipt_payload()['lines'][0],
                'quantity': None,
                'quantity_certainty': 'unknown',
            }]),
            format='json',
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        posted = self.client.post(
            f"{self.receipt_url}{draft.data['pk']}/post/",
            {},
            format='json',
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        lot = StockLot.objects.get(receipt_line__receipt_id=draft.data['pk'])
        self.assertIsNone(lot.base_unit_cost)
        return lot

    def test_a_lot_of_unknown_quantity_reports_stock_it_cannot_value(self):
        """An unopened packet has a balance once counted but still no unit cost."""
        lot = self.unknown_lot()

        self.assertEqual(self.rows_by_location(lot.pk), {})

        counted = self.client.post(
            f'{self.movement_url}adjust/',
            {
                'lot': lot.pk,
                'quantity': '40.000000000',
                'unit_code': UnitCode.MILLILITRE,
                'direction': 'gain',
                'location': self.store.pk,
                'reason': 'Counted the packet contents',
            },
            format='json',
        )
        self.assertEqual(counted.status_code, 201, counted.data)

        row = self.rows_by_location(lot.pk)[self.store.pk]
        self.assertEqual(row['physical_quantity'], '40.000000000')
        self.assertIsNone(row['base_unit_cost'])
        self.assertIsNone(row['valuation'])

    def test_a_packet_sown_before_it_was_counted_still_shows_where_it_went(self):
        """An unknown lot's only movement can be outbound, and that is its history.

        Nothing ever moved seed into the packet, because the receipt could not
        say how much was in it. Reading the destination side alone would leave
        the packet with no location at all, so the row that shows it has gone
        negative -- the one signal that the count is overdue -- would vanish.
        """
        lot = self.unknown_lot()
        sowing = MovementRequest(
            lot=lot,
            movement_type=StockMovement.MovementType.CONSUMPTION,
            quantity=Decimal('20'),
            source=self.store,
            reference='Sown before counting',
            enforce_source_balance=False,
        )
        post_stock_movement(self.workspace, self.user, sowing)

        row = self.rows_by_location(lot.pk)[self.store.pk]
        self.assertEqual(row['physical_quantity'], '-20.000000000')
        self.assertIsNone(row['valuation'])

    def test_balances_stop_at_the_edge_of_the_workspace(self):
        """Another nursery's stock is not part of this one's valuation."""
        self.create_opening(quantity='500.000000000')
        rows = self.rows()
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(lot=row['lot']):
                self.assertEqual(
                    StockLot.objects.get(pk=row['lot']).workspace_id,
                    self.workspace.pk,
                )


class InventoryQueryFilterTests(LedgerRestFixture):
    """Every inventory list filter refuses a malformed value by name.

    The parsers raise without a key of their own, which reads as a body error
    on a request that has no body. Each entry below names one filter that a
    view actually reads, so a filter that started silently ignoring bad input
    -- or answering with a 500 -- is caught rather than quietly dropped.
    """

    # url, parameter, a value of the wrong shape for that parameter's type.
    filters = (
        ('/inventory/balances/', 'item', 'all'),
        ('/inventory/balances/', 'lot', 'all'),
        ('/inventory/balances/', 'location', 'store'),
        ('/inventory/balances/', 'expires_before', '2026-13-01'),
        ('/inventory/balances/', 'low_stock', 'yes'),
        ('/inventory/lots/', 'item', 'all'),
        ('/inventory/lots/', 'expires_before', 'soon'),
        ('/inventory/movements/', 'lot', 'all'),
        ('/inventory/movements/', 'item', 'all'),
        ('/inventory/movements/', 'unit', 'all'),
        ('/inventory/movements/', 'location', 'store'),
        ('/inventory/movements/', 'movement_type', 'borrowed'),
        ('/inventory/movements/', 'occurred_after', 'yesterday'),
        ('/inventory/movements/', 'occurred_before', '2026-08-32T00:00:00Z'),
        ('/inventory/receipts/', 'supplier', 'all'),
        ('/inventory/receipts/', 'status', 'nearly'),
        ('/inventory/receipts/', 'received_after', 'yesterday'),
        ('/inventory/receipts/', 'received_before', '2026-02-30'),
        ('/inventory/receipts/', 'seed_packet', 'yes'),
        ('/inventory/input-tax-adjustments/', 'receipt', 'all'),
        ('/inventory/input-tax-adjustments/', 'receipt_line', 'all'),
        ('/inventory/serialized-units/', 'item', 'all'),
        ('/inventory/serialized-units/', 'location', 'store'),
        ('/inventory/serialized-units/', 'active', 'yes'),
        ('/inventory/serialized-units/', 'in_use', 'yes'),
        ('/inventory/serialized-units/', 'physical_state', 'composted'),
    )

    def test_a_malformed_filter_is_a_field_error_against_its_own_parameter(self):
        """The message arrives on the parameter the caller can correct."""
        for url, parameter, value in self.filters:
            with self.subTest(url=url, parameter=parameter):
                response = self.client.get(url, {parameter: value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(parameter, response.data)

    def test_an_absent_filter_narrows_nothing(self):
        """A parser that read a missing value as a bad one would hide every row."""
        self.create_opening(quantity='500.000000000')
        seen = set()
        for url, _parameter, _value in self.filters:
            if url in seen:
                continue
            seen.add(url)
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self.client.get(self.balance_url).data), 1)
