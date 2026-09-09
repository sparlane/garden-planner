"""Pot fill claims share inventory availability with sales and numbering."""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import skipUnlessDBFeature
from django.utils import timezone

from applications.services import ApplicationRequest, LineRequest, TargetRequest, create_application_draft
from inventory.ledger import (
    IndividualizationRequest,
    MovementRequest,
    UnitMovementRequest,
    bulk_balance,
    discard_numbering,
    filled_bulk,
    individualize_lot_units,
    physical_balance,
    post_stock_movement,
    post_unit_movement,
    unpromised_bulk,
)
from inventory.models import InventoryItem, InventoryUnit, QuantityCertainty, StockMovement
from locations.models import Location
from sales.commerce import post_fulfillment
from sales.models import SalesOrderLine
from sales.services import LotRequest, allocate_targets, confirm_order
from sales.test_concurrency import ReservationConcurrencyTestCase
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import make_location, make_seed_tray_generation, make_specific_plant_location, make_stock_lot
from workspaces.models import Workspace, get_current_workspace

from .container_fills import clean_empty_fill, open_counted_fill, open_numbered_fill
from .generations import CloseRequest, close_generation, reopen_generation
from .models import SeedTrayGeneration


class PotFillOpeningTests(CountedStockTestCase):
    """Opening claims pots without consuming them or inventing unit identities."""

    def setUp(self):
        super().setUp()
        self.lot = self.receive(quantity='100')

    def fill(self, count=50, **kwargs):
        """Open a run of anonymous pots at the fixture's store."""
        return open_counted_fill(self.workspace, self.user, self.lot, self.store, count, **kwargs)

    def test_opening_preserves_stock_cost_and_records_one_fill_and_event(self):
        """Fifty filled pots are still fifty pots on hand, with no numbering."""
        movements = list(self.lot.movements.values_list('pk', flat=True))
        opened_at = timezone.now()
        fill = self.fill(opened_at=opened_at, notes='Buffer for next week.')
        self.assertEqual(fill.container_count, 50)
        self.assertEqual(fill.source_location, self.store)
        self.assertEqual(fill.opened_at, opened_at)
        self.assertEqual(fill.notes, 'Buffer for next week.')
        self.assertEqual(fill.events.get().created_by, self.user)
        self.assertEqual(fill.events.get().event_type, 'opened')
        self.assertEqual(physical_balance(self.lot, self.store), 100)
        self.assertEqual(bulk_balance(self.lot, self.store), 100)
        self.assertEqual(filled_bulk(self.lot, self.store), 50)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 50)
        self.assertFalse(InventoryUnit.objects.filter(source_lot=self.lot).exists())
        self.assertEqual(list(self.lot.movements.values_list('pk', flat=True)), movements)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.base_unit_cost, Decimal('0.5'))

    def test_multiple_runs_cannot_overclaim_one_pool(self):
        """A second run sees only the empty pots the first left behind."""
        self.fill(60)
        with self.assertRaises(ValidationError):
            self.fill(41)
        self.fill(40)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)
        self.assertEqual(self.lot.container_fills.count(), 2)

    def test_claims_are_scoped_to_lot_location_and_open_status(self):
        """Cleaning releases the claim, while historical counts remain immutable."""
        fill = self.fill()
        elsewhere = make_location()
        other_lot = make_stock_lot(item=self.item, location=self.store)
        self.assertEqual(filled_bulk(self.lot, elsewhere), 0)
        self.assertEqual(filled_bulk(other_lot, self.store), 0)
        clean_empty_fill(self.workspace, self.user, fill, reason='Empty pots cleaned.')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 100)
        fill.refresh_from_db()
        self.assertEqual(fill.container_count, 50)

    def test_invalid_counts_and_unavailable_targets_leave_no_fill(self):
        """Validate whole counts and workspace identity before recording a claim."""
        for count in (0, -1, Decimal('1.5'), 'NaN', 101):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                self.fill(count)
        other = Workspace.objects.create(name='Other nursery')
        with self.assertRaises(ValidationError):
            open_counted_fill(other, self.user, self.lot, self.store, 1)
        with self.assertRaises(ValidationError):
            open_counted_fill(self.workspace, self.user, self.lot, make_location(workspace=other), 1)
        self.assertFalse(self.lot.container_fills.exists())

    def test_unknown_inactive_and_quarantined_stock_cannot_be_filled(self):
        """An opening requires a usable container and a known physical pool."""
        for obj, field, value in (
            (self.lot, 'quantity_certainty', QuantityCertainty.UNKNOWN),
            (self.item, 'active', False),
            (self.store, 'active', False),
            (self.store, 'location_type', Location.LocationType.QUARANTINE),
        ):
            original = getattr(obj, field)
            setattr(obj, field, value)
            type(obj).objects.filter(pk=obj.pk).update(**{field: getattr(obj, field)})
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.fill()
            setattr(obj, field, original)
            type(obj).objects.filter(pk=obj.pk).update(**{field: getattr(obj, field)})

    def test_lot_tracking_also_protects_filled_stock(self):
        """Anonymous pots need the same protection without optional numbering."""
        self.item.tracking_mode = InventoryItem.TrackingMode.LOT
        self.item.save()
        self.fill(100)
        with self.assertRaises(ValidationError):
            post_stock_movement(self.workspace, self.user, MovementRequest(
                self.lot, StockMovement.MovementType.CONSUMPTION, Decimal('1'), source=self.store,
            ))

    def test_stock_actions_cannot_take_filled_pots_even_with_balance_override(self):
        """Movement, disposal and consumption cannot detach a counted fill from its pots."""
        self.fill(60)
        destination = make_location()
        for kind in ('transfer', 'consumption', 'waste', 'sale', 'adjustment_loss'):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                post_stock_movement(self.workspace, self.user, MovementRequest(
                    self.lot, kind, Decimal('41'), source=self.store,
                    destination=destination if kind == 'transfer' else None,
                    reason='Stock action', enforce_source_balance=False,
                ))
        self.assertEqual(physical_balance(self.lot, self.store), 100)

    def test_numbering_draws_only_empty_pots(self):
        """Moving a filled pot to an identity needs the later fill-aware workflow."""
        self.fill(60)
        with self.assertRaises(ValidationError):
            self.number(self.lot, 41)
        units = self.number(self.lot, 40)
        self.assertEqual(len(units), 40)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)

    def test_reservations_and_fills_share_the_pool_but_dispatch_honours_its_reservation(self):
        """A reserved stack may ship while the filled pots remain untouched."""
        line = self.counted_line(quantity=40)
        allocation = allocate_targets(line, self.user, lot_requests=[LotRequest(self.lot.pk, self.store.pk, 40)])[0]
        confirm_order(line.order, self.user)
        with self.assertRaises(ValidationError):
            self.fill(61)
        self.fill(60)
        post_fulfillment(line.order, self.user, operation_key=uuid4(), allocation_ids=[allocation.pk])
        self.assertEqual(physical_balance(self.lot, self.store), 60)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 0)

    def test_pending_sales_recheck_the_pool_at_confirmation(self):
        """A draft is not a hold and must observe fills opened since selection."""
        line = self.counted_line(quantity=60)
        allocate_targets(line, self.user, lot_requests=[LotRequest(self.lot.pk, self.store.pk, 60)])
        self.fill(50)
        with self.assertRaises(ValidationError):
            confirm_order(line.order, self.user)


class NumberedPotFillTests(CountedStockTestCase):
    """A numbered pot has one locked fill history and retains its own cost."""

    def setUp(self):
        super().setUp()
        self.lot = self.receive(quantity='2')
        self.unit = self.number(self.lot, 1)[0]

    def fill(self):
        """Open the fixture's numbered pot."""
        return open_numbered_fill(self.workspace, self.user, self.unit)

    def test_opening_retains_identity_cost_and_does_not_claim_bulk_twice(self):
        """Numbering already took the pot out of the anonymous pool."""
        fill = self.fill()
        self.assertEqual(fill.inventory_unit, self.unit)
        self.assertEqual(fill.container_count, 1)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 1)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.acquisition_cost, Decimal('0.5'))
        with self.assertRaises(ValidationError):
            self.fill()

    def test_fill_history_prevents_discarding_numbering(self):
        """Even a cleaned fill makes an identity more than a numbering typo."""
        fill = self.fill()
        clean_empty_fill(self.workspace, self.user, fill, reason='Empty pots cleaned.')
        with self.assertRaisesMessage(ValidationError, 'fill history'):
            discard_numbering(self.workspace, self.unit)
        self.assertEqual(self.fill().sequence, 2)

    def test_filled_unit_can_move_but_cannot_leave_stock(self):
        """Its stable identity carries the fill to another bench."""
        fill = self.fill()
        for kind in ('sale', 'waste', 'adjustment_loss'):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                post_unit_movement(self.workspace, self.user, UnitMovementRequest(self.unit, kind, reason='Remove'))
        destination = make_location()
        post_unit_movement(self.workspace, self.user, UnitMovementRequest(self.unit, 'transfer', destination))
        self.unit.refresh_from_db()
        fill.refresh_from_db()
        self.assertEqual(self.unit.current_location, destination)
        self.assertEqual(fill.inventory_unit, self.unit)

    def test_numbered_sales_recheck_fills_at_confirmation(self):
        """The pot cannot be promised as empty after its fill was opened."""
        line = self.counted_line(quantity=1, line_type=SalesOrderLine.LineType.UNIT)
        allocate_targets(line, self.user, unit_ids=[self.unit.pk])
        self.fill()
        with self.assertRaises(ValidationError):
            confirm_order(line.order, self.user)

    def test_reserved_unit_cannot_be_filled(self):
        """A confirmed order owns the claim before the filling operation."""
        line = self.counted_line(quantity=1, line_type=SalesOrderLine.LineType.UNIT)
        allocate_targets(line, self.user, unit_ids=[self.unit.pk])
        confirm_order(line.order, self.user)
        with self.assertRaises(ValidationError):
            self.fill()


class EmptyPotFillCleaningTests(CountedStockTestCase):
    """An explicit clean releases only containers whose contents are accounted for."""

    def setUp(self):
        super().setUp()
        self.lot = self.receive(quantity='100')
        self.fill = open_counted_fill(self.workspace, self.user, self.lot, self.store, 50)

    def clean(self, **kwargs):
        """Clean the fixture's fill with an operator's reason."""
        return clean_empty_fill(self.workspace, self.user, self.fill, reason='Washed empty pots.', **kwargs)

    def test_clean_releases_stock_without_movements_and_preserves_audit(self):
        """Released pots can be numbered while the original fill remains on file."""
        movements = list(self.lot.movements.values_list('pk', flat=True))
        occurred_at = timezone.now()
        fill = self.clean(occurred_at=occurred_at)
        self.assertEqual(fill.status, 'closed')
        self.assertEqual(fill.closed_at, occurred_at)
        self.assertEqual(fill.closed_by, self.user)
        self.assertEqual(fill.close_reason, 'Washed empty pots.')
        event = fill.events.get(event_type='closed')
        self.assertEqual(event.occurred_at, occurred_at)
        self.assertEqual(event.created_by, self.user)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 100)
        self.assertEqual(fill.container_count, 50)
        self.assertEqual(list(self.lot.movements.values_list('pk', flat=True)), movements)
        self.assertEqual(len(self.number(self.lot, 100)), 100)
        with self.assertRaises(ValidationError):
            self.clean()
        self.assertEqual(fill.events.count(), 2)

    def test_invalid_clean_leaves_claim_and_audit_unchanged(self):
        """A clean needs a reason and cannot precede opening."""
        with self.assertRaises(ValidationError):
            clean_empty_fill(self.workspace, self.user, self.fill, reason='  ')
        with self.assertRaises(ValidationError):
            self.clean(occurred_at=self.fill.opened_at - timedelta(seconds=1))
        self.assertEqual(unpromised_bulk(self.lot, self.store), 50)
        self.assertEqual(self.fill.events.count(), 1)

    def test_tray_services_cannot_clean_or_reopen_pot_fills(self):
        """Tray residual accounting cannot substitute for pot stock locking."""
        with self.assertRaises(ValidationError):
            close_generation(self.fill, self.user, CloseRequest(reason='Clean'))
        self.clean()
        with self.assertRaises(ValidationError):
            reopen_generation(self.fill, self.user, 'Correction')
        self.assertEqual(unpromised_bulk(self.lot, self.store), 100)

    def test_pot_service_rejects_trays_and_foreign_fills(self):
        """The operation is scoped to this workspace's pot fills."""
        tray_fill = make_seed_tray_generation()
        with self.assertRaises(ValidationError):
            clean_empty_fill(self.workspace, self.user, tray_fill, reason='Clean')
        other = Workspace.objects.create(name='Other nursery')
        with self.assertRaises(SeedTrayGeneration.DoesNotExist):
            clean_empty_fill(other, self.user, self.fill, reason='Clean')
        self.assertEqual(self.fill.events.count(), 1)

    def test_numbered_clean_refuses_plants_and_pending_inputs(self):
        """An empty-only clean cannot silently discard physical contents."""
        unit = self.number(self.lot, 1)[0]
        fill = open_numbered_fill(self.workspace, self.user, unit)
        placement = make_specific_plant_location(location_type='container_unit', container_unit=unit, seed_tray_cell=None)
        with self.assertRaisesMessage(ValidationError, 'Move the plants'):
            clean_empty_fill(self.workspace, self.user, fill, reason='Clean')
        placement.ended = timezone.now()
        placement.save()
        create_application_draft(self.workspace, self.user, ApplicationRequest(
            applied_at=timezone.now(), source_location=self.store,
            lines=(LineRequest(
                item=self.item, lot=self.lot, applied_quantity=1, unit_code='each',
                usage_basis='manual', targets=(TargetRequest('inventory_unit', unit),),
            ),),
        ))
        with self.assertRaisesMessage(ValidationError, 'input applications'):
            clean_empty_fill(self.workspace, self.user, fill, reason='Clean')
        self.assertEqual(fill.events.count(), 1)


@skipUnlessDBFeature('has_select_for_update')
class PotFillConcurrencyTests(ReservationConcurrencyTestCase):
    """First fills and competing stock claims serialize on an existing stock row."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.user = get_user_model().objects.create_user(username='fill-racer')
        self.store = make_location()
        self.lot = make_stock_lot(location=self.store)
        self.lot.item.category = InventoryItem.Category.POT_CONTAINER
        self.lot.item.base_unit = 'each'
        self.lot.item.tracking_mode = InventoryItem.TrackingMode.MIXED
        self.lot.item.save()

    def race(self, first, second):
        """Start two independent transactions together and surface unexpected errors."""
        barrier = Barrier(2)

        def attempt(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                return 'opened'
            except ValidationError:
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, [first, second])), ['opened', 'rejected'])

    def fill(self):
        """Claim more than half of the anonymous pool."""
        return open_counted_fill(self.workspace, None, self.lot, self.store, 60)

    def test_competing_counted_fills_cannot_overclaim(self):
        """Locking the lot works even before either fill exists."""
        self.race(self.fill, self.fill)
        self.assertEqual(self.lot.container_fills.count(), 1)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 40)

    def test_fill_and_numbering_share_the_lot_lock(self):
        """Neither numbering nor filling can win the same pots twice."""
        self.race(self.fill, lambda: individualize_lot_units(
            self.workspace, None, IndividualizationRequest(self.lot, self.store, 60),
        ))
        self.assertEqual(unpromised_bulk(self.lot, self.store), 40)

    def test_competing_numbered_fills_have_one_winner(self):
        """The unit lock closes the first-fill gap before a unique constraint fires."""
        unit = individualize_lot_units(self.workspace, None, IndividualizationRequest(self.lot, self.store, 1))[0]

        def operation():
            return open_numbered_fill(self.workspace, None, unit)

        self.race(operation, operation)
        self.assertEqual(unit.container_fills.count(), 1)

    def test_fill_and_order_confirmation_share_the_lot_lock(self):
        """An earlier draft must compete with filling when it becomes a reservation."""
        order, line = self._order_with_line(SalesOrderLine.LineType.LOT_QUANTITY, item=self.lot.item, quantity=60)
        allocate_targets(line, self.user, lot_requests=[LotRequest(self.lot.pk, self.store.pk, 60)])
        self.race(self.fill, lambda: confirm_order(order, self.user))
        self.assertEqual(unpromised_bulk(self.lot, self.store), 40)

    def test_fill_and_stock_transfer_share_the_lot_lock(self):
        """A counted fill cannot retain pots that a concurrent transfer already took."""
        destination = make_location()
        self.race(self.fill, lambda: post_stock_movement(
            self.workspace, self.user, MovementRequest(
                self.lot, 'transfer', Decimal('60'), source=self.store, destination=destination,
            ),
        ))
        self.assertEqual(unpromised_bulk(self.lot, self.store), 40)

    def test_numbered_fill_and_reservation_share_the_unit_lock(self):
        """The first fill and an order cannot independently claim the same identity."""
        unit = individualize_lot_units(self.workspace, self.user, IndividualizationRequest(self.lot, self.store, 1))[0]
        order, line = self._order_with_line(SalesOrderLine.LineType.UNIT, item=self.lot.item)
        allocate_targets(line, self.user, unit_ids=[unit.pk])
        self.race(lambda: open_numbered_fill(self.workspace, self.user, unit), lambda: confirm_order(order, self.user))

    def test_competing_cleans_record_one_release(self):
        """The fill is closed only once even when two operators clean it together."""
        fill = self.fill()
        self.race(
            lambda: clean_empty_fill(self.workspace, self.user, fill, reason='Clean'),
            lambda: clean_empty_fill(self.workspace, self.user, fill, reason='Clean'),
        )
        self.assertEqual(fill.events.filter(event_type='closed').count(), 1)
        self.assertEqual(unpromised_bulk(self.lot, self.store), 100)
