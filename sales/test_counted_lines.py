"""Selling anonymous stock by the count rather than by identity.

A nursery that buys five hundred pots to get a trade price sells the surplus
as a stack, not as five hundred asset codes. These cover the line type, the
quantity-bearing allocation, and the arithmetic that decides how much of a lot
is still free — which is arithmetic rather than a unique index, because many
customers may legitimately hold parts of one lot at once.
"""

from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.ledger import (
    IndividualizationRequest,
    bulk_balance,
    individualize_lot_units,
    unpromised_bulk,
)
from inventory.models import InventoryItem, StockLot, StockMovement
from inventory.units import UnitCode
from locations.models import Location
from tests.factories import make_stock_lot
from workspaces.models import Workspace, get_current_workspace

from .commerce import post_fulfillment, post_return, reverse_fulfillment
from .models import (
    FulfillmentLine,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesReturnLine,
)
from .services import (
    LotRequest,
    allocate_targets,
    close_reservations,
    confirm_order,
    create_order,
    preview_targets,
)


class CountedStockTestCase(TestCase):
    """A nursery holding one boxed pot item received as anonymous bulk."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='counted-user')
        self.client.force_login(self.user)
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P9 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.MIXED,
        )
        self.store = Location.objects.create(
            workspace=self.workspace,
            name='Pot store',
            code='POT-STORE',
            location_type=Location.LocationType.STORAGE,
        )

    def receive(self, quantity='500', unit_cost='0.5000', item=None):
        """Stock one anonymous lot of pots at the store, priced per pot."""
        quantity = Decimal(quantity)
        return make_stock_lot(
            item=item or self.item,
            location=self.store,
            quantity=quantity,
            base_unit_cost=Decimal(unit_cost),
            acquisition_total=quantity * Decimal(unit_cost),
        )

    def number(self, lot, count):
        """Individualise part of a lot the way the catalog screen does."""
        return individualize_lot_units(
            self.workspace, self.user,
            IndividualizationRequest(lot=lot, location=self.store, count=count),
        )

    def counted_line(self, order=None, quantity=50, **overrides):
        """Build one saved counted line for the fixture's pot item."""
        values = {
            'order': order or create_order(self.workspace, self.user),
            'line_type': SalesOrderLine.LineType.LOT_QUANTITY,
            'item': self.item,
            'description': 'Fifty loose pots',
            'quantity': quantity,
            'unit_price': Decimal('0.8000'),
            'tax_rate': Decimal('15'),
        }
        values.update(overrides)
        line = SalesOrderLine(**values)
        line.save()
        return line


class CountedLineTargetTests(CountedStockTestCase):
    """A counted line promises an item by the count, never by identity."""

    def build(self, **overrides):
        """Build an unsaved counted line so validation can be inspected."""
        values = {
            'order': create_order(self.workspace, self.user),
            'line_type': SalesOrderLine.LineType.LOT_QUANTITY,
            'item': self.item,
            'description': 'Fifty loose pots',
            'quantity': 50,
            'unit_price': Decimal('0.8000'),
            'tax_rate': Decimal('15'),
        }
        values.update(overrides)
        return SalesOrderLine(**values)

    def test_a_mixed_item_counted_in_each_is_a_valid_counted_line(self):
        """Bulk pots out of a mixed lot are exactly what this line sells."""
        line = self.build()

        line.full_clean()
        line.save()

        self.assertEqual(line.item_id, self.item.pk)

    def test_a_purely_lot_tracked_item_is_also_valid(self):
        """Nothing about the mechanism needs the item to be numberable."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()

        self.build().full_clean()

    def test_a_serialized_item_is_refused(self):
        """Every unit of a serialized item is somebody's identity already."""
        self.item.tracking_mode = InventoryItem.TrackingMode.SERIALIZED
        self.item.save()

        with self.assertRaises(ValidationError) as context:
            self.build().full_clean()
        self.assertIn('item', context.exception.message_dict)

    def test_an_item_measured_by_weight_is_refused(self):
        """Counting is whole units; measured stock is task 114's problem."""
        media = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Potting mix',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.LITRE,
            tracking_mode=InventoryItem.TrackingMode.LOT,
        )

        with self.assertRaises(ValidationError) as context:
            self.build(item=media).full_clean()
        self.assertIn('item', context.exception.message_dict)

    def test_a_variety_is_refused_on_a_counted_line(self):
        """A pot is not a plant, so there is no variety to promise."""
        with self.assertRaises(ValidationError) as context:
            self.build(item=None).full_clean()
        self.assertIn('item', context.exception.message_dict)


class CountedAllocationShapeTests(CountedStockTestCase):
    """The quantity-bearing allocation keeps every identity rule literal."""

    def test_a_lot_allocation_records_its_lot_place_and_count(self):
        """Anonymous stock has no identity, so the promise carries a figure."""
        lot = self.receive()
        line = self.counted_line()

        allocation = SalesOrderAllocation.objects.create(
            line=line, stock_lot=lot, source_location=self.store, quantity=50,
        )

        self.assertEqual(allocation.target_kind, 'stock_lot')
        self.assertEqual(allocation.quantity, 50)

    def test_a_lot_allocation_without_a_place_is_refused(self):
        """Stock is only available somewhere, so a draw has to say where."""
        lot = self.receive()
        line = self.counted_line()

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, quantity=50,
            )

    def test_a_lot_allocation_without_a_quantity_is_refused(self):
        """Without a count the allocation says nothing about how much."""
        lot = self.receive()
        line = self.counted_line()

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store,
            )

    def test_a_lot_from_another_item_is_refused(self):
        """The line named an item; a lot of something else is not it."""
        other = InventoryItem.objects.create(
            workspace=self.workspace,
            name='P11 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.LOT,
        )
        lot = self.receive(item=other)
        line = self.counted_line()

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store, quantity=5,
            )
        self.assertIn('stock_lot', context.exception.message_dict)

    def test_a_lot_cannot_be_allocated_to_an_identity_line(self):
        """A unit line allocates identities; a count is not one."""
        lot = self.receive()
        line = self.counted_line(line_type=SalesOrderLine.LineType.UNIT, quantity=1)

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(
                line=line, stock_lot=lot, source_location=self.store, quantity=1,
            )
        self.assertIn('stock_lot', context.exception.message_dict)

    def test_a_numbered_unit_cannot_be_allocated_to_a_counted_line(self):
        """Numbered stock leaves as itself, not as an anonymous count."""
        lot = self.receive()
        pot = self.number(lot, 1)[0]
        line = self.counted_line()

        with self.assertRaises(ValidationError) as context:
            SalesOrderAllocation.objects.create(line=line, inventory_unit=pot)
        self.assertIn('inventory_unit', context.exception.message_dict)

    def test_an_identity_allocation_still_carries_no_quantity(self):
        """The existing invariant stays literally true, not merely implied."""
        lot = self.receive()
        pot = self.number(lot, 1)[0]
        line = self.counted_line(
            line_type=SalesOrderLine.LineType.UNIT, quantity=1,
        )

        with self.assertRaises(ValidationError):
            SalesOrderAllocation.objects.create(
                line=line, inventory_unit=pot, quantity=1,
            )

    def test_a_counted_allocation_is_immutable_once_written(self):
        """Rewriting a promised count would rewrite what was reserved."""
        lot = self.receive()
        line = self.counted_line()
        allocation = SalesOrderAllocation.objects.create(
            line=line, stock_lot=lot, source_location=self.store, quantity=50,
        )

        allocation.quantity = 60
        with self.assertRaises(ValidationError):
            allocation.save()


class CountedAvailabilityTests(CountedStockTestCase):
    """How much of a lot is free is arithmetic over reservations and bulk."""

    def draw(self, line, lot, quantity, location=None):
        """Attach one counted draw through the service the screens call."""
        return allocate_targets(line, self.user, lot_requests=[
            LotRequest(lot.pk, (location or self.store).pk, quantity),
        ])

    def test_a_reservation_lowers_what_the_next_order_may_have(self):
        """Fifty promised pots are fifty the next customer cannot be sold."""
        lot = self.receive()
        line = self.counted_line()
        self.draw(line, lot, 50)
        confirm_order(line.order, self.user)

        self.assertEqual(unpromised_bulk(lot, self.store), Decimal('450'))

    def test_two_orders_may_hold_parts_of_one_lot_at_once(self):
        """No identity line allows this, and every bulk sale depends on it."""
        lot = self.receive()
        for _ in range(2):
            line = self.counted_line()
            self.draw(line, lot, 50)
            confirm_order(line.order, self.user)

        self.assertEqual(unpromised_bulk(lot, self.store), Decimal('400'))
        self.assertEqual(
            SalesOrderAllocation.objects.filter(
                stock_lot=lot, status=SalesOrderAllocation.Status.RESERVED,
            ).count(),
            2,
        )

    def test_a_draw_beyond_what_is_left_is_refused(self):
        """The pool is finite, and the refusal names the figure it enforced."""
        lot = self.receive(quantity='60')
        held = self.counted_line()
        self.draw(held, lot, 50)
        confirm_order(held.order, self.user)

        line = self.counted_line(quantity=20)
        with self.assertRaises(ValidationError) as context:
            self.draw(line, lot, 20)
        self.assertIn('insufficient_stock', str(context.exception))

    def test_numbered_pots_are_not_part_of_the_anonymous_pool(self):
        """A pot with a code on it is standing in its own right, not in bulk."""
        lot = self.receive()
        self.number(lot, 6)

        self.assertEqual(unpromised_bulk(lot, self.store), Decimal('494'))

    def test_numbering_the_last_loose_pots_takes_them_off_a_later_draw(self):
        """Individualising and reserving compete for one pool, not for two."""
        lot = self.receive(quantity='10')
        self.number(lot, 8)
        line = self.counted_line(quantity=5)

        with self.assertRaises(ValidationError):
            self.draw(line, lot, 5)
        self.assertEqual(self.draw(line, lot, 2)[0].quantity, 2)

    def test_a_reservation_leaves_too_little_to_number(self):
        """The competition runs both ways, or the pool is oversold one way."""
        lot = self.receive(quantity='10')
        line = self.counted_line(quantity=9)
        self.draw(line, lot, 9)
        confirm_order(line.order, self.user)

        with self.assertRaises(ValidationError):
            self.number(lot, 2)

    def test_confirming_adds_a_whole_order_s_draws_before_deciding(self):
        """Two lines drawing on one lot cannot each be promised the same pots."""
        order = create_order(self.workspace, self.user)
        lot = self.receive(quantity='60')
        for _ in range(2):
            line = self.counted_line(order=order, quantity=40)
            self.draw(line, lot, 40)

        with self.assertRaises(ValidationError) as context:
            confirm_order(order, self.user)
        self.assertIn('unpromised', str(context.exception))

    def test_a_released_reservation_returns_its_pots_to_the_pool(self):
        """Stock held for an order that went away is stock anybody may buy."""
        lot = self.receive()
        line = self.counted_line()
        allocation = self.draw(line, lot, 50)[0]
        confirm_order(line.order, self.user)

        close_reservations(line.order, self.user, [allocation.pk], 'release')

        self.assertEqual(unpromised_bulk(lot, self.store), Decimal('500'))


class CountedSelectionPreviewTests(CountedStockTestCase):
    """The preview answers for a whole basket the way allocation will."""

    def test_a_draw_that_fits_reports_what_is_free_behind_it(self):
        """An operator sees the figure the refusal would have been measured on."""
        lot = self.receive()
        line = self.counted_line()

        preview = preview_targets(line, lot_requests=[
            LotRequest(lot.pk, self.store.pk, 50),
        ])

        self.assertEqual(preview['conflicts'], [])
        self.assertEqual(preview['selected'], [{
            'id': lot.pk,
            'location': self.store.pk,
            'quantity': 50,
            'available': '500.000000000',
        }])

    def test_two_draws_on_one_lot_are_answered_in_order(self):
        """The second request is told what the first one left, not the pool."""
        lot = self.receive(quantity='60')
        line = self.counted_line(quantity=60)

        preview = preview_targets(line, lot_requests=[
            LotRequest(lot.pk, self.store.pk, 50),
            LotRequest(lot.pk, self.store.pk, 30),
        ])

        self.assertEqual(len(preview['selected']), 1)
        self.assertEqual(preview['conflicts'][0]['reason'], 'insufficient_stock')
        self.assertEqual(preview['conflicts'][0]['available'], '10.000000000')

    def test_every_counted_refusal_is_reachable(self):
        """A reason the preview can give, allocation has to give as well."""
        lot = self.receive(quantity='10')
        other = self.receive(quantity='10', item=InventoryItem.objects.create(
            workspace=self.workspace,
            name='P11 pot',
            category=InventoryItem.Category.POT_CONTAINER,
            base_unit=UnitCode.EACH,
            tracking_mode=InventoryItem.TrackingMode.LOT,
        ))
        line = self.counted_line(quantity=20)
        cases = {
            'wrong_item': LotRequest(other.pk, self.store.pk, 1),
            'unknown_location': LotRequest(lot.pk, self.store.pk + 10_000, 1),
            'insufficient_stock': LotRequest(lot.pk, self.store.pk, 20),
            'unknown': LotRequest(lot.pk + 10_000, self.store.pk, 1),
        }
        for reason, request in cases.items():
            with self.subTest(reason=reason):
                preview = preview_targets(line, lot_requests=[request])

                self.assertEqual(preview['selected'], [])
                self.assertEqual(preview['conflicts'][0]['reason'], reason)
                with self.assertRaises(ValidationError):
                    allocate_targets(line, self.user, lot_requests=[request])

    def test_a_counted_line_refuses_identities_before_any_lock(self):
        """A pot with a code on it does not go out as an anonymous count."""
        lot = self.receive()
        pot = self.number(lot, 1)[0]
        line = self.counted_line()

        with self.assertRaises(ValidationError) as context:
            preview_targets(line, unit_ids=[pot.pk])
        self.assertIn('units', context.exception.message_dict)

    def test_an_identity_line_refuses_a_counted_draw(self):
        """The refusal runs the other way too, and for the same reason."""
        lot = self.receive()
        line = self.counted_line(
            line_type=SalesOrderLine.LineType.UNIT, quantity=1,
        )

        with self.assertRaises(ValidationError) as context:
            preview_targets(line, lot_requests=[
                LotRequest(lot.pk, self.store.pk, 1),
            ])
        self.assertIn('lots', context.exception.message_dict)


class CountedFulfillmentTests(CountedStockTestCase):
    """Dispatching counted stock moves it once and costs it from its lot."""

    def sell(self, quantity=50, lot=None, unit_cost='0.5000'):
        """Take one counted line all the way to a posted fulfillment."""
        lot = lot or self.receive(unit_cost=unit_cost)
        line = self.counted_line(quantity=quantity)
        allocation = allocate_targets(line, self.user, lot_requests=[
            LotRequest(lot.pk, self.store.pk, quantity),
        ])[0]
        confirm_order(line.order, self.user)
        fulfillment = post_fulfillment(
            line.order, self.user, operation_key=uuid4(),
            allocation_ids=[allocation.pk],
        )
        return lot, line, fulfillment

    def test_one_sale_movement_carries_the_whole_allocation(self):
        """The pots left as a stack, and the ledger says so in one row."""
        lot, _line, fulfillment = self.sell()

        movements = StockMovement.objects.filter(
            lot=lot, movement_type=StockMovement.MovementType.SALE,
        )
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.get().quantity, Decimal('50'))
        self.assertEqual(movements.get().source_id, self.store.pk)
        self.assertEqual(fulfillment.lines.get().stock_movement_id, movements.get().pk)

    def test_cost_of_sale_comes_from_the_lot_that_shipped(self):
        """Fifty pots at fifty cents is what those fifty pots cost."""
        _lot, _line, fulfillment = self.sell()

        line = fulfillment.lines.get()
        self.assertEqual(line.cogs_amount, Decimal('25.0000'))
        self.assertFalse(line.cogs_provisional)

    def test_an_unpriced_lot_yields_an_unknown_cost_not_a_zero(self):
        """Substituting nought would understate every total built on it."""
        lot = self.receive()
        StockLot.objects.filter(pk=lot.pk).update(
            base_unit_cost=None, acquisition_total=None,
        )
        lot.refresh_from_db()

        _lot, _line, fulfillment = self.sell(lot=lot)

        line = fulfillment.lines.get()
        self.assertIsNone(line.cogs_amount)
        self.assertTrue(line.cogs_provisional)

    def test_the_whole_line_is_recognised_by_one_dispatch(self):
        """Fifty positions of money leave on the one line that shipped them."""
        _lot, line, fulfillment = self.sell()

        fulfillment_line = fulfillment.lines.get()
        self.assertEqual(fulfillment_line.subtotal_ex_tax, line.subtotal_ex_tax)
        self.assertEqual(fulfillment_line.total_incl_tax, line.total_incl_tax)
        self.assertEqual(fulfillment_line.commercial_position, 1)

    def test_a_fully_dispatched_counted_order_is_fulfilled(self):
        """Status follows what shipped, not how many rows recorded it."""
        _lot, line, _fulfillment = self.sell()

        line.order.refresh_from_db()
        self.assertEqual(line.order.status, SalesOrder.Status.FULFILLED)

    def test_a_part_dispatch_leaves_the_order_partially_fulfilled(self):
        """Twenty of fifty is twenty, however few rows carried them."""
        lot = self.receive()
        line = self.counted_line(quantity=50)
        first, second = allocate_targets(line, self.user, lot_requests=[
            LotRequest(lot.pk, self.store.pk, 20),
            LotRequest(lot.pk, self.store.pk, 30),
        ])
        confirm_order(line.order, self.user)

        post_fulfillment(
            line.order, self.user, operation_key=uuid4(),
            allocation_ids=[first.pk],
        )

        line.order.refresh_from_db()
        self.assertEqual(line.order.status, SalesOrder.Status.PARTIALLY_FULFILLED)
        post_fulfillment(
            line.order, self.user, operation_key=uuid4(),
            allocation_ids=[second.pk],
        )
        line.order.refresh_from_db()
        self.assertEqual(line.order.status, SalesOrder.Status.FULFILLED)

    def test_two_dispatches_split_the_line_money_exactly(self):
        """Every position is recognised once and the parts add back."""
        lot = self.receive()
        line = self.counted_line(quantity=50)
        allocations = allocate_targets(line, self.user, lot_requests=[
            LotRequest(lot.pk, self.store.pk, 20),
            LotRequest(lot.pk, self.store.pk, 30),
        ])
        confirm_order(line.order, self.user)
        for allocation in allocations:
            post_fulfillment(
                line.order, self.user, operation_key=uuid4(),
                allocation_ids=[allocation.pk],
            )

        shipped = FulfillmentLine.objects.filter(fulfillment__order=line.order)
        self.assertEqual(
            sum(row.total_incl_tax for row in shipped), line.total_incl_tax,
        )
        self.assertEqual(
            sorted(row.commercial_position for row in shipped), [1, 21],
        )

    def test_dispatching_lowers_the_bulk_balance_by_what_left(self):
        """The reservation is consumed, and the stock is genuinely gone."""
        lot, _line, _fulfillment = self.sell()

        self.assertEqual(bulk_balance(lot, self.store), Decimal('450'))
        self.assertEqual(unpromised_bulk(lot, self.store), Decimal('450'))

    def test_reversing_a_dispatch_restores_the_stock(self):
        """A reversal puts the pots back where they were promised from."""
        lot, _line, fulfillment = self.sell()

        reverse_fulfillment(
            fulfillment, self.user, operation_key=uuid4(),
            reason='Wrong customer.',
        )

        self.assertEqual(bulk_balance(lot, self.store), Decimal('500'))


class CountedReturnTests(CountedStockTestCase):
    """Counted stock comes back whole or the return says why it cannot."""

    def setUp(self):
        super().setUp()
        self.lot = self.receive()
        self.line = self.counted_line(quantity=50)
        allocation = allocate_targets(self.line, self.user, lot_requests=[
            LotRequest(self.lot.pk, self.store.pk, 50),
        ])[0]
        confirm_order(self.line.order, self.user)
        self.fulfillment = post_fulfillment(
            self.line.order, self.user, operation_key=uuid4(),
            allocation_ids=[allocation.pk],
        )
        self.shipped = self.fulfillment.lines.get()

    def send_back(self, **overrides):
        """Post one return of the whole counted dispatch."""
        item = {
            'fulfillment_line': self.shipped,
            'outcome': SalesReturnLine.Outcome.AVAILABLE,
            'destination': self.store,
        }
        item.update(overrides)
        return post_return(
            self.line.order, self.user, operation_key=uuid4(),
            items=[item], reason='Customer changed their mind.',
        )

    def test_a_whole_return_puts_the_stock_back(self):
        """Fifty pots came back, so fifty are on hand and free again."""
        self.send_back()

        self.assertEqual(bulk_balance(self.lot, self.store), Decimal('500'))
        self.assertEqual(unpromised_bulk(self.lot, self.store), Decimal('500'))

    def test_a_partial_return_is_refused_and_names_the_limit(self):
        """Splitting posted money is task 114's rewrite, not a silent guess."""
        with self.assertRaises(ValidationError) as context:
            self.send_back(quantity=20)
        self.assertIn('50', str(context.exception))
        self.assertEqual(bulk_balance(self.lot, self.store), Decimal('450'))

    def test_a_discarded_return_takes_the_stock_back_and_wastes_it(self):
        """Both facts are recorded, rather than the stock never returning."""
        sales_return = self.send_back(
            outcome=SalesReturnLine.Outcome.DISCARDED, destination=None,
        )

        line = sales_return.lines.get()
        self.assertIsNotNone(line.return_movement_id)
        self.assertEqual(
            line.discard_movement.movement_type,
            StockMovement.MovementType.WASTE,
        )
        self.assertEqual(bulk_balance(self.lot, self.store), Decimal('450'))

    def test_a_returned_dispatch_reopens_the_order(self):
        """Nothing is dispatched any more, so nothing has been fulfilled."""
        self.send_back()

        self.line.order.refresh_from_db()
        self.assertEqual(self.line.order.status, SalesOrder.Status.CONFIRMED)
