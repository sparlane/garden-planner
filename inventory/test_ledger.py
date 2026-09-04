"""Transactional behavior tests for exact-lot inventory services."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from supplies.models import Supplier
from workspaces.models import get_current_workspace

from .ledger import (
    MovementRequest,
    OpeningBalanceRequest,
    physical_balance,
    physical_balances,
    post_opening_balance,
    post_receipt,
    post_stock_movement,
    post_stocktake,
    reverse_movement,
    reverse_receipt,
    reverse_stocktake,
)
from .models import (
    InventoryItem,
    QuantityCertainty,
    StockLot,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
    Stocktake,
    StocktakeLine,
)
from .units import UnitCode


class LedgerFixtureTestCase(TestCase):
    """A workspace with a costed media item, two locations, and a supplier."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='ledger-service-user')
        self.supplier = Supplier.objects.create(
            workspace=self.workspace,
            name='Service Supplier',
        )
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Service media',
            category=InventoryItem.Category.GROWING_MEDIA,
            base_unit=UnitCode.MILLILITRE,
        )
        self.store = Location.objects.create(
            workspace=self.workspace,
            name='Store',
            code='STORE',
            location_type=Location.LocationType.STORAGE,
        )
        self.growing = Location.objects.create(
            workspace=self.workspace,
            name='Propagation house',
            code='PROP',
            location_type=Location.LocationType.GROWING,
        )

    def make_receipt(self, **overrides):
        """Create a draft receipt with workspace financial defaults."""
        values = {
            'workspace': self.workspace,
            'supplier': self.supplier,
            'received_date': date(2026, 8, 1),
            'currency_code': self.workspace.currency_code,
            'created_by': self.user,
        }
        values.update(overrides)
        return StockReceipt.objects.create(**values)

    def add_receipt_line(self, receipt, **overrides):
        """Add one normalized media line to a draft receipt."""
        values = {
            'receipt': receipt,
            'item': self.item,
            'quantity': Decimal('2'),
            'unit_code': UnitCode.LITRE,
            'base_quantity': Decimal('2000'),
            'line_cost_ex_tax': Decimal('10'),
            'supplier_cost_incl_tax': Decimal('11.5'),
            'tax_treatment': StockReceiptLine.TaxTreatment.STANDARD,
            'tax_rate': Decimal('15'),
            'input_tax_source': StockReceiptLine.InputTaxSource.SUPPLIER,
            'input_tax_amount': Decimal('1.5'),
            'destination': self.store,
        }
        values.update(overrides)
        return StockReceiptLine.objects.create(**values)

    def make_opening(self, quantity=Decimal('100')):
        """Post a costed opening balance into the store."""
        return post_opening_balance(
            self.workspace,
            self.user,
            OpeningBalanceRequest(
                item=self.item,
                quantity=quantity,
                destination=self.store,
                acquisition_total=Decimal('25'),
                received_on=date(2026, 8, 1),
                reason='Audited opening balance',
            ),
        )


class LedgerServiceTests(LedgerFixtureTestCase):
    """Posting services maintain balances and document audit trails atomically."""

    def test_post_receipt_creates_exact_lots_costs_and_balances(self):
        """Every line becomes one immutable valued lot and receipt movement."""
        receipt = self.make_receipt(supplier_reference='INV-100')
        first_line = self.add_receipt_line(receipt)
        second_item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Labels',
            category=InventoryItem.Category.LABEL,
            base_unit=UnitCode.EACH,
        )
        second_line = self.add_receipt_line(
            receipt,
            item=second_item,
            quantity=Decimal('100'),
            unit_code=UnitCode.EACH,
            base_quantity=Decimal('100'),
            line_cost_ex_tax=Decimal('20'),
            supplier_cost_incl_tax=Decimal('23'),
            input_tax_amount=Decimal('3'),
        )

        posted, lots = post_receipt(receipt, self.user)

        self.assertEqual(posted.status, StockReceipt.Status.POSTED)
        self.assertEqual(len(lots), 2)
        first_lot = StockLot.objects.get(receipt_line=first_line)
        second_lot = StockLot.objects.get(receipt_line=second_line)
        self.assertTrue(first_lot.identifier.startswith('LOT-'))
        self.assertNotEqual(first_lot.identifier, second_lot.identifier)
        self.assertEqual(first_lot.acquisition_total, Decimal('11.5000'))
        self.assertEqual(first_lot.base_unit_cost, Decimal('0.005750000000'))
        self.assertEqual(physical_balance(first_lot, self.store), Decimal('2000'))
        self.assertEqual(physical_balance(second_lot, self.store), Decimal('100'))
        self.assertIsNotNone(self.item.refresh_from_db() or self.item.stock_history_started_at)
        self.assertEqual(
            StockMovement.objects.filter(
                receipt_line__receipt=receipt,
                movement_type=StockMovement.MovementType.RECEIPT,
            ).count(),
            2,
        )

    def test_receipt_validation_rolls_back_every_line(self):
        """An invalid later line prevents every lot and movement from posting."""
        receipt = self.make_receipt()
        self.add_receipt_line(receipt)
        inactive = Location.objects.create(
            workspace=self.workspace,
            name='Closed store',
            code='CLOSED',
            location_type=Location.LocationType.STORAGE,
            active=False,
        )
        self.add_receipt_line(receipt, destination=inactive)

        with self.assertRaises(ValidationError):
            post_receipt(receipt, self.user)

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.Status.DRAFT)
        self.assertFalse(StockLot.objects.filter(receipt_line__receipt=receipt).exists())
        self.assertFalse(StockMovement.objects.filter(receipt_line__receipt=receipt).exists())

    def test_receipt_requires_current_workspace_currency(self):
        """V1 does not silently treat a foreign-currency amount as local cost."""
        receipt = self.make_receipt(currency_code='USD')
        self.add_receipt_line(receipt)
        with self.assertRaises(ValidationError) as context:
            post_receipt(receipt, self.user)
        self.assertIn('currency_code', context.exception.message_dict)

    def test_transfer_consumption_and_reversal_reconcile_locations(self):
        """Location effects remain explicit while total physical stock reconciles."""
        lot, _opening = self.make_opening()
        transfer = post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=Decimal('40'),
                source=self.store,
                destination=self.growing,
            ),
        )
        consumption = post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('10'),
                source=self.growing,
                reference='Propagation batch 1',
            ),
        )

        self.assertEqual(physical_balance(lot, self.store), Decimal('60'))
        self.assertEqual(physical_balance(lot, self.growing), Decimal('30'))
        reverse_movement(consumption, self.user, 'Consumption entered twice')
        self.assertEqual(physical_balance(lot, self.growing), Decimal('40'))
        reverse_movement(transfer, self.user, 'Wrong destination')
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))
        self.assertEqual(physical_balance(lot, self.growing), Decimal('0'))

    def test_outbound_and_inbound_reversals_cannot_create_negative_stock(self):
        """Consumption and receipt reversals validate the currently affected place."""
        lot, opening = self.make_opening(Decimal('10'))
        post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('6'),
                source=self.store,
            ),
        )
        with self.assertRaises(ValidationError) as context:
            post_stock_movement(
                self.workspace,
                self.user,
                MovementRequest(
                    lot=lot,
                    movement_type=StockMovement.MovementType.WASTE,
                    quantity=Decimal('5'),
                    source=self.store,
                    reason='Damaged',
                ),
            )
        self.assertIn('quantity', context.exception.message_dict)
        with self.assertRaises(ValidationError):
            reverse_movement(opening, self.user, 'Opening was wrong')

    def test_receipt_reversal_is_document_scoped_and_atomic(self):
        """Receipt rows cannot diverge from their header reversal state."""
        receipt = self.make_receipt()
        self.add_receipt_line(receipt)
        posted, lots = post_receipt(receipt, self.user)
        original = StockMovement.objects.get(receipt_line__receipt=receipt)
        with self.assertRaises(ValidationError):
            reverse_movement(original, self.user, 'Wrong delivery')

        reversed_receipt, reversals = reverse_receipt(
            posted,
            self.user,
            'Supplier delivery cancelled',
        )
        self.assertEqual(reversed_receipt.status, StockReceipt.Status.REVERSED)
        self.assertEqual(len(reversals), 1)
        self.assertEqual(physical_balance(lots[0], self.store), Decimal('0'))

    def test_stocktake_posts_and_reverses_explicit_variances(self):
        """A count snapshots expected stock and keeps its adjustment linkage."""
        lot, _opening = self.make_opening()
        stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            counted_at=timezone.now(),
            notes='Weekly count',
            created_by=self.user,
        )
        line = StocktakeLine.objects.create(
            stocktake=stocktake,
            lot=lot,
            location=self.store,
            counted_quantity=Decimal('90'),
            unit_code=UnitCode.MILLILITRE,
            counted_base_quantity=Decimal('90'),
            reason='Container spill',
        )

        posted, movements = post_stocktake(stocktake, self.user)
        line.refresh_from_db()
        self.assertEqual(posted.status, Stocktake.Status.POSTED)
        self.assertEqual(line.expected_base_quantity, Decimal('100'))
        self.assertEqual(line.variance_base_quantity, Decimal('-10'))
        self.assertEqual(movements[0].movement_type, StockMovement.MovementType.ADJUSTMENT_LOSS)
        self.assertEqual(physical_balance(lot, self.store), Decimal('90'))

        reversed_stocktake, reversals = reverse_stocktake(
            posted,
            self.user,
            'Count used the wrong container',
        )
        self.assertEqual(reversed_stocktake.status, Stocktake.Status.REVERSED)
        self.assertEqual(len(reversals), 1)
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))

    def test_adjustment_and_waste_require_reasons(self):
        """Unexplained corrections cannot enter the audit trail."""
        lot, _opening = self.make_opening()
        for movement_type, source, destination in (
            (StockMovement.MovementType.ADJUSTMENT_GAIN, None, self.store),
            (StockMovement.MovementType.ADJUSTMENT_LOSS, self.store, None),
            (StockMovement.MovementType.WASTE, self.store, None),
        ):
            with self.subTest(movement_type=movement_type):
                with self.assertRaises(ValidationError) as context:
                    post_stock_movement(
                        self.workspace,
                        self.user,
                        MovementRequest(
                            lot=lot,
                            movement_type=movement_type,
                            quantity=Decimal('1'),
                            source=source,
                            destination=destination,
                        ),
                    )
                self.assertIn('reason', context.exception.message_dict)


class BatchedBalanceTests(LedgerFixtureTestCase):
    """The batched balances agree with the single-lot figure, in one query."""

    def test_one_query_answers_for_every_lot_and_place_at_once(self):
        """A caller serving a page of rows must not pay an aggregate per row."""
        first, _opening = self.make_opening(Decimal('100'))
        second, _second_opening = self.make_opening(Decimal('40'))
        post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=first,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=Decimal('30'),
                source=self.store,
                destination=self.growing,
            ),
        )
        post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=second,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('10'),
                source=self.store,
                reference='Potting run',
            ),
        )

        with self.assertNumQueries(1):
            balances = physical_balances([first, second])

        self.assertEqual(balances, {
            (first.pk, self.store.pk): Decimal('70'),
            (first.pk, self.growing.pk): Decimal('30'),
            (second.pk, self.store.pk): Decimal('30'),
        })
        for lot in (first, second):
            for location in (self.store, self.growing):
                with self.subTest(lot=lot.pk, location=location.pk):
                    self.assertEqual(
                        balances[(lot.pk, location.pk)],
                        physical_balance(lot, location),
                    )

    def test_a_place_the_stock_has_left_keeps_a_zero_entry(self):
        """The balance screen reports an emptied place rather than dropping it."""
        lot, _opening = self.make_opening(Decimal('100'))
        post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=Decimal('100'),
                source=self.store,
                destination=self.growing,
            ),
        )

        balances = physical_balances([lot])

        # Membership first: reading a missing key would create it.
        self.assertIn((lot.pk, self.store.pk), balances)
        self.assertEqual(balances[(lot.pk, self.store.pk)], Decimal('0'))
        self.assertEqual(balances[(lot.pk, self.growing.pk)], Decimal('100'))

    def test_a_pair_the_ledger_never_touched_reads_zero(self):
        """Callers index the mapping by row, so a gap must not be a KeyError."""
        lot, _opening = self.make_opening(Decimal('100'))

        balances = physical_balances([lot])

        self.assertEqual(balances[(lot.pk, self.growing.pk)], Decimal('0'))
        with self.assertNumQueries(0):
            self.assertEqual(physical_balances([]), {})


class ReversalChainTests(LedgerFixtureTestCase):
    """A row is reversed once, by the document that owns it, and no further.

    Reversal is how every correction is made in this ledger, so the guards
    around it are the ones that decide whether a balance can be walked back to
    a figure nobody posted. None of them had a test.
    """

    def consume(self, lot, quantity=Decimal('10')):
        """Take stock out of the store as an ordinary standalone movement."""
        return post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=quantity,
                source=self.store,
                reference='Propagation batch 1',
            ),
        )

    def counted_stocktake(self, lot, counted=Decimal('90')):
        """Post a count that finds less stock than the ledger expected."""
        stocktake = Stocktake.objects.create(
            workspace=self.workspace,
            counted_at=timezone.now(),
            notes='Weekly count',
            created_by=self.user,
        )
        StocktakeLine.objects.create(
            stocktake=stocktake,
            lot=lot,
            location=self.store,
            counted_quantity=counted,
            unit_code=UnitCode.MILLILITRE,
            counted_base_quantity=counted,
            reason='Container spill',
        )
        return post_stocktake(stocktake, self.user)

    def test_a_reversal_cannot_itself_be_reversed(self):
        """Undoing a correction means posting a fact, not negating a negation."""
        lot, _opening = self.make_opening()
        reversal = reverse_movement(
            self.consume(lot), self.user, 'Consumption entered twice',
        )
        with self.assertRaises(ValidationError) as caught:
            reverse_movement(reversal, self.user, 'Reversal entered twice')
        self.assertIn('movement', caught.exception.message_dict)
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))

    def test_a_movement_is_reversed_only_once(self):
        """A second reversal would take the stock out twice."""
        lot, _opening = self.make_opening()
        consumption = self.consume(lot)
        reverse_movement(consumption, self.user, 'Consumption entered twice')
        with self.assertRaises(ValidationError) as caught:
            reverse_movement(consumption, self.user, 'And again')
        self.assertIn('movement', caught.exception.message_dict)
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))

    def test_a_reversal_states_why_it_was_needed(self):
        """A correction with no reason cannot enter the audit trail."""
        lot, _opening = self.make_opening()
        consumption = self.consume(lot)
        for reason in ('', '   '):
            with self.subTest(reason=repr(reason)):
                with self.assertRaises(ValidationError) as caught:
                    reverse_movement(consumption, self.user, reason)
                self.assertIn('reason', caught.exception.message_dict)
        self.assertEqual(physical_balance(lot, self.store), Decimal('90'))

    def test_stocktake_rows_are_reversed_only_through_their_stocktake(self):
        """A count restores every variance it posted, or none of them."""
        lot, _opening = self.make_opening()
        _posted, movements = self.counted_stocktake(lot)
        with self.assertRaisesMessage(
                ValidationError, 'Reverse stocktake movements through their stocktake.'):
            reverse_movement(movements[0], self.user, 'Counted the wrong container')
        self.assertEqual(physical_balance(lot, self.store), Decimal('90'))

    def test_a_document_reverses_the_rows_it_posted_only_once(self):
        """A second document reversal would restore the same stock twice."""
        lot, _opening = self.make_opening()
        posted, _movements = self.counted_stocktake(lot)
        reverse_stocktake(posted, self.user, 'Counted the wrong container')
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))
        with self.assertRaises(ValidationError) as caught:
            reverse_stocktake(posted, self.user, 'And again')
        self.assertIn('status', caught.exception.message_dict)
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))

        receipt = self.make_receipt()
        self.add_receipt_line(receipt)
        received, lots = post_receipt(receipt, self.user)
        reverse_receipt(received, self.user, 'Supplier delivery cancelled')
        with self.assertRaises(ValidationError) as caught:
            reverse_receipt(received, self.user, 'And again')
        self.assertIn('status', caught.exception.message_dict)
        self.assertEqual(physical_balance(lots[0], self.store), Decimal('0'))

    def test_a_reversed_stocktake_row_cannot_be_reversed_a_second_way(self):
        """Neither route may undo a variance the other already undid."""
        lot, _opening = self.make_opening()
        posted, movements = self.counted_stocktake(lot)
        reverse_stocktake(posted, self.user, 'Counted the wrong container')
        with self.assertRaises(ValidationError) as caught:
            reverse_movement(movements[0], self.user, 'Counted the wrong container')
        self.assertIn('movement', caught.exception.message_dict)
        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))

    def test_a_chain_of_reversals_returns_every_location_to_where_it_started(self):
        """The ledger is walked back fact by fact, not by rewriting rows."""
        lot, _opening = self.make_opening()
        transfer = post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.TRANSFER,
                quantity=Decimal('40'),
                source=self.store,
                destination=self.growing,
            ),
        )
        consumption = post_stock_movement(
            self.workspace,
            self.user,
            MovementRequest(
                lot=lot,
                movement_type=StockMovement.MovementType.CONSUMPTION,
                quantity=Decimal('25'),
                source=self.growing,
                reference='Propagation batch 1',
            ),
        )
        posted, variances = self.counted_stocktake(lot, counted=Decimal('50'))
        self.assertEqual(physical_balance(lot, self.store), Decimal('50'))

        reverse_stocktake(posted, self.user, 'Counted the wrong container')
        reverse_movement(consumption, self.user, 'Consumption entered twice')
        reverse_movement(transfer, self.user, 'Wrong destination')

        self.assertEqual(physical_balance(lot, self.store), Decimal('100'))
        self.assertEqual(physical_balance(lot, self.growing), Decimal('0'))
        # Every original row survives its correction, so the history still
        # shows what was posted as well as what was undone.
        self.assertEqual(
            set(
                StockMovement.objects.filter(lot=lot, reversal_of__isnull=False)
                .values_list('reversal_of_id', flat=True)
            ),
            {transfer.pk, consumption.pk, variances[0].pk},
        )
        self.assertEqual(
            StockMovement.objects.filter(lot=lot, reversal_of__isnull=True).count(), 4,
        )


class UnknownQuantityReversalTests(TestCase):
    """A lot of unknown quantity has no balance to hold a reversal to.

    An unopened seed packet is truthfully sowable, so its container goes
    negative as seed comes out of it. Anything that later has to take stock back
    out of that container — correcting a return, say — cannot be measured
    against a figure that was never known.
    """

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='unknown-lot-user')
        self.item = InventoryItem.objects.create(
            workspace=self.workspace,
            name='Unopened packet contents',
            category=InventoryItem.Category.SEED,
            base_unit=UnitCode.SEED,
        )
        self.container = Location.objects.create(
            workspace=self.workspace,
            name='Seed packet',
            code='PACKET-UNKNOWN',
            location_type=Location.LocationType.SEED_PACKET,
        )
        self.lot = StockLot.objects.create(
            workspace=self.workspace,
            item=self.item,
            origin=StockLot.Origin.OPENING,
            received_on=date(2026, 1, 1),
            initial_base_quantity=None,
            quantity_certainty=QuantityCertainty.UNKNOWN,
            currency_code=self.workspace.currency_code,
        )

    def _put_back(self, quantity):
        """Record seed going back into the packet it came out of."""
        return post_stock_movement(self.workspace, self.user, MovementRequest(
            lot=self.lot,
            movement_type=StockMovement.MovementType.ADJUSTMENT_GAIN,
            quantity=Decimal(quantity),
            destination=self.container,
            occurred_at=timezone.now(),
            reason='Unsown seed returned.',
        ))

    def _sow(self, quantity):
        """Take seed out of a packet whose contents were never counted."""
        return post_stock_movement(self.workspace, self.user, MovementRequest(
            lot=self.lot,
            movement_type=StockMovement.MovementType.CONSUMPTION,
            quantity=Decimal(quantity),
            source=self.container,
            occurred_at=timezone.now(),
            reason='Sown.',
            enforce_source_balance=False,
        ))

    def test_a_return_can_be_reversed_from_a_negative_container(self):
        """The container is negative by design, not by error."""
        self._sow('8')
        gain = self._put_back('2')
        self.assertEqual(physical_balance(self.lot, self.container), Decimal('-6'))

        reversal = reverse_movement(gain, self.user, 'Recorded against the wrong sowing.')

        self.assertEqual(reversal.movement_type, StockMovement.MovementType.REVERSAL)
        self.assertEqual(physical_balance(self.lot, self.container), Decimal('-8'))

    def _unknown_receipt(self):
        """Post a seed receipt that records a packet without counting it."""
        receipt = StockReceipt.objects.create(
            workspace=self.workspace,
            supplier=Supplier.objects.create(
                workspace=self.workspace, name='Packet supplier',
            ),
            received_date=date(2026, 8, 1),
            currency_code=self.workspace.currency_code,
            created_by=self.user,
        )
        StockReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            quantity=None,
            quantity_certainty=QuantityCertainty.UNKNOWN,
            unit_code=UnitCode.SEED,
            base_quantity=None,
            line_cost_ex_tax=Decimal('4'),
            destination=self.container,
        )
        return post_receipt(receipt, self.user)

    def test_an_unused_unknown_receipt_reverses_although_it_moved_no_stock(self):
        """There was never a quantity to walk back, only a document."""
        posted, lots = self._unknown_receipt()
        self.assertEqual(StockMovement.objects.filter(lot=lots[0]).count(), 0)

        reversed_receipt, reversals = reverse_receipt(
            posted, self.user, 'Packet returned unopened.',
        )

        self.assertEqual(reversed_receipt.status, StockReceipt.Status.REVERSED)
        self.assertEqual(reversals, [])

    def test_an_unknown_receipt_cannot_be_reversed_once_seed_has_been_sown(self):
        """Undoing the packet would leave sowings drawn from nothing."""
        posted, lots = self._unknown_receipt()
        post_stock_movement(self.workspace, self.user, MovementRequest(
            lot=lots[0],
            movement_type=StockMovement.MovementType.CONSUMPTION,
            quantity=Decimal('12'),
            source=self.container,
            occurred_at=timezone.now(),
            reason='Sown.',
            enforce_source_balance=False,
        ))

        with self.assertRaises(ValidationError) as caught:
            reverse_receipt(posted, self.user, 'Packet returned unopened.')

        self.assertIn('status', caught.exception.message_dict)
        posted.refresh_from_db()
        self.assertEqual(posted.status, StockReceipt.Status.POSTED)

    def test_a_counted_lot_still_has_its_balance_enforced(self):
        """Relaxing the check where a figure exists would hide real errors."""
        StockLot.objects.filter(pk=self.lot.pk).update(
            quantity_certainty=QuantityCertainty.EXACT,
            initial_base_quantity=Decimal('10'),
        )
        self.lot.refresh_from_db()
        gain = self._put_back('2')
        self._sow('5')

        with self.assertRaises(ValidationError) as caught:
            reverse_movement(gain, self.user, 'Wrong sowing.')

        self.assertIn('quantity', caught.exception.message_dict)
