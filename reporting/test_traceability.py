"""Both trace directions reconciled against the cost layers underneath them."""

# Test method names carry the contract.
# pylint: disable=missing-function-docstring

from decimal import Decimal
from uuid import uuid4

from applications.services import reverse_application
from costing.services import plant_cost_breakdown
from costing.test_services import CostingServiceTestCase
from inventory.ledger import MovementRequest, physical_balance, post_stock_movement
from inventory.models import StockMovement
from locations.models import Location
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from sales.models import FulfillmentLine
from tests.factories import make_location
from workspaces.models import Workspace


class TraceabilityReconciliationTests(CostingServiceTestCase):
    """One plant raised from a costed packet and a costed lot of media."""

    def setUp(self):
        super().setUp()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.application = self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def trace(self, path):
        """Return one traceability report body."""
        response = self.client.get(f'/reports/traceability/{path}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def plant_trace(self):
        """Return the backward trace for the fixture's one plant."""
        return self.trace(f'plants/{self.plant.pk}')

    def lot_trace(self, lot_id):
        """Return the forward trace for one input lot."""
        return self.trace(f'lots/{lot_id}')

    def plant_layers(self):
        """Return the cost layers that still count against the plant."""
        return [
            layer for layer in self.effective()
            if layer.specific_plant_id == self.plant.pk
        ]

    def test_the_plant_trace_is_its_cost_layers_and_nothing_else(self):
        body = self.plant_trace()

        layers = self.plant_layers()
        self.assertEqual(
            [row['allocation_id'] for row in body['results']],
            [layer.pk for layer in layers],
        )
        self.assertEqual(body['reconciliation']['cost_layers'], len(layers))
        for row, layer in zip(body['results'], layers):
            with self.subTest(allocation=layer.pk):
                self.assertEqual(
                    Decimal(row['cost_amount']),
                    layer.amount,
                )
                self.assertEqual(
                    Decimal(row['quantity']),
                    layer.base_quantity,
                )

    def test_every_layer_names_the_lot_the_input_actually_came_out_of(self):
        body = self.plant_trace()

        self.assertEqual(
            {row['lot_id'] for row in body['results']},
            {self.packet.stock_lot_id, self.media_lot.pk},
        )
        for row in body['results']:
            with self.subTest(lot=row['lot_id']):
                self.assertIsNotNone(row['item_id'])
                self.assertIsNotNone(row['lot_identifier'])

    def test_the_traced_layers_add_up_to_the_cost_the_plant_is_carrying(self):
        body = self.plant_trace()

        breakdown = plant_cost_breakdown(self.plant)
        self.assertEqual(
            sum(Decimal(row['cost_amount']) for row in body['results']),
            Decimal(breakdown['provisional_value']),
        )
        self.assertEqual(
            body['totals']['provisional_value'],
            breakdown['provisional_value'],
        )

    def test_each_lot_the_plant_names_traces_forward_to_that_plant(self):
        named = {row['lot_id'] for row in self.plant_trace()['results']}

        self.assertTrue(named)
        for lot_id in named:
            with self.subTest(lot=lot_id):
                forward = self.lot_trace(lot_id)
                self.assertIn(
                    self.plant.pk,
                    {row['plant_id'] for row in forward['results']},
                )

    def test_the_lot_traces_quantity_is_the_sum_of_the_rows_it_lists(self):
        body = self.lot_trace(self.media_lot.pk)

        self.assertTrue(body['results'])
        self.assertEqual(
            Decimal(body['reconciliation']['allocation_quantity']),
            sum(Decimal(row['quantity']) for row in body['results']),
        )
        self.assertEqual(
            body['totals']['allocations'],
            len(body['results']),
        )
        self.assertEqual(
            body['totals']['affected_plants'],
            len({row['plant_id'] for row in body['results'] if row['plant_id']}),
        )

    def test_remaining_balances_are_the_ledger_balances_that_are_not_zero(self):
        body = self.lot_trace(self.media_lot.pk)

        balances = body['totals']['remaining_balances']
        self.assertTrue(balances)
        for balance in balances:
            with self.subTest(location=balance['location_id']):
                self.assertEqual(
                    Decimal(balance['quantity']),
                    physical_balance(
                        self.media_lot,
                        Location.objects.get(pk=balance['location_id']),
                    ),
                )
        # Fifty litres received, forty millilitres applied.
        self.assertEqual(
            sum(Decimal(row['quantity']) for row in balances),
            Decimal('49.96'),
        )

    def test_a_place_the_lot_has_left_is_not_carried_as_a_zero_balance(self):
        elsewhere = make_location(workspace=self.workspace)
        remaining = physical_balance(self.media_lot, self.location)
        post_stock_movement(self.workspace, self.user, MovementRequest(
            lot=self.media_lot,
            movement_type=StockMovement.MovementType.TRANSFER,
            quantity=remaining,
            source=self.location,
            destination=elsewhere,
        ))

        balances = self.lot_trace(self.media_lot.pk)['totals']['remaining_balances']

        self.assertEqual(physical_balance(self.media_lot, self.location), 0)
        self.assertEqual(
            [balance['location_id'] for balance in balances],
            [elsewhere.pk],
        )

    def test_a_reversed_fulfillment_leaves_the_plants_commercial_history(self):
        order_pk, fulfillment = self.sell_the_plant()
        self.assertEqual(self.plant_trace()['totals']['fulfillments'], 1)

        reversed_response = self.client.post(
            f'/sales/orders/{order_pk}'
            f"/fulfillments/{fulfillment['pk']}/reverse/",
            {'operation_key': str(uuid4()), 'reason': 'Entered in error.'},
            format='json',
        )
        self.assertEqual(reversed_response.status_code, 201, reversed_response.data)

        backward = self.plant_trace()
        self.assertEqual(backward['results'][0]['commerce'], [])
        self.assertEqual(backward['totals']['fulfillments'], 0)
        forward = self.lot_trace(self.packet.stock_lot_id)
        row = next(
            entry for entry in forward['results']
            if entry['plant_id'] == self.plant.pk
        )
        self.assertEqual(row['fulfillments'], [])

    def test_a_reversed_application_leaves_both_traces_and_the_plants_cost(self):
        reverse_application(self.application, self.user, 'Wrong tray.')
        self.reallocate()

        backward = self.plant_trace()
        self.assertEqual(
            {row['lot_id'] for row in backward['results']},
            {self.packet.stock_lot_id},
        )
        self.assertEqual(backward['totals']['provisional_value'], '1.0000')

        forward = self.lot_trace(self.media_lot.pk)
        self.assertEqual(forward['totals']['allocations'], 0)
        self.assertEqual(forward['totals']['affected_plants'], 0)

    def test_a_sale_reaches_both_traces_and_matches_the_fulfillment_line(self):
        _order_pk, fulfillment = self.sell_the_plant()
        line = FulfillmentLine.objects.get(fulfillment__pk=fulfillment['pk'])

        backward = self.plant_trace()
        commerce = backward['results'][0]['commerce']
        self.assertEqual(len(commerce), 1)
        self.assertEqual(backward['totals']['fulfillments'], 1)
        self.assertEqual(commerce[0]['fulfillment_line_id'], line.pk)
        self.assertEqual(
            Decimal(commerce[0]['revenue_ex_tax']), line.subtotal_ex_tax,
        )
        self.assertEqual(Decimal(commerce[0]['cogs_amount']), line.cogs_amount)

        forward = self.lot_trace(self.packet.stock_lot_id)
        row = next(
            entry for entry in forward['results']
            if entry['plant_id'] == self.plant.pk
        )
        self.assertEqual(
            [entry['fulfillment_line_id'] for entry in row['fulfillments']],
            [line.pk],
        )

    def sell_the_plant(self):
        """Take the fixture's plant through order, confirmation, and dispatch."""
        record_lifecycle_event(
            self.plant, self.user, OutcomeRequest(EventType.READY),
        )
        order = self.client.post(
            '/sales/orders/', {'status': 'draft'}, format='json',
        )
        self.assertEqual(order.status_code, 201, order.data)
        line = self.client.post('/sales/order-lines/', {
            'order': order.data['pk'],
            'line_type': 'seedling',
            'variety': self.plant.batch.variety_id,
            'description': 'One seedling',
            'quantity': 1,
            'unit_price': '10.0000',
            'tax_rate': '15.0000',
        }, format='json')
        self.assertEqual(line.status_code, 201, line.data)
        allocated = self.client.post(
            f"/sales/orders/{order.data['pk']}/allocate/",
            {'line': line.data['pk'], 'plant_ids': [self.plant.pk]},
            format='json',
        )
        self.assertEqual(allocated.status_code, 201, allocated.data)
        confirmed = self.client.post(
            f"/sales/orders/{order.data['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        fulfilled = self.client.post(
            f"/sales/orders/{order.data['pk']}/fulfillments/",
            {
                'operation_key': str(uuid4()),
                'allocation_ids': [allocated.data[0]['pk']],
            },
            format='json',
        )
        self.assertEqual(fulfilled.status_code, 201, fulfilled.data)
        return order.data['pk'], fulfilled.data
