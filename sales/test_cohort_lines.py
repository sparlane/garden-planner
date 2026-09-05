"""Selling nursery stock that was deliberately never given identities.

Two hundred lettuce seedlings leave as a count, not as two hundred plant
records with their own lifecycles, cost allocations and labels. These cover the
cohort line type, the quantity-bearing allocation against a block, the
arithmetic that decides how much of a block is still free to promise, and what
a return of a count means when nothing came back that can be told apart.
"""

from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from health.models import HealthObservation, HealthObservationType
from plantings.cohort_availability import available_quantity, reserved_quantity
from plantings.cohorts import change_cohort, observe_cohort, promote_cohort
from plantings.models import CohortOperation, PlantCohort, SpecificPlant
from tests.factories import (
    make_location,
    make_nursery_workspace,
    make_production_batch,
    quarantine_stock,
)

from .commerce import (
    post_fulfillment,
    post_return,
    record_payment,
    reverse_fulfillment,
    reverse_return,
)
from .models import (
    Payment,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
    SalesReturnLine,
)
from .services import (
    CohortRequest,
    allocate_targets,
    confirm_order,
    create_order,
    preview_targets,
)


class CohortStockTestCase(TestCase):
    """A nursery holding one available block of anonymous seedlings."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace()
        self.user = get_user_model().objects.create_user(username='cohort-user')
        self.batch = make_production_batch()
        self.bench = make_location(name='Cohort bench', code='COHORT-BENCH')
        self.cohort = self.available_cohort()

    def observe(self, quantity=200, batch=None):
        """Record one anonymous block through the public cohort service."""
        cohort, _operation = observe_cohort(
            self.workspace, self.user,
            batch=batch or self.batch,
            quantity=quantity,
            location=self.bench,
            idempotency_key=uuid4(),
        )
        return cohort

    def make_available(self, cohort):
        """Put a growing block on sale, which is what a cohort line needs."""
        cohort, _operation = change_cohort(
            self.workspace, self.user,
            cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.READY,
            idempotency_key=uuid4(),
        )
        return cohort

    def available_cohort(self, quantity=200, batch=None):
        """Observe and offer one block in the state a sale needs it in."""
        return self.make_available(self.observe(quantity, batch))

    def quarantine(self, cohort):
        """Open a quarantine case over one block without changing its state."""
        return quarantine_stock(
            self.workspace, self.user, [{'type': 'cohort', 'id': cohort.pk}],
        )

    def line_values(self, **overrides):
        """Return the terms a cohort line in this fixture is built from."""
        return dict({
            'order': create_order(self.workspace, self.user),
            'line_type': SalesOrderLine.LineType.COHORT_QUANTITY,
            'variety': self.batch.variety,
            'description': 'Fifty lettuce seedlings',
            'quantity': 50,
            'unit_price': Decimal('0.9000'),
            'tax_rate': Decimal('15'),
        }, **overrides)

    def cohort_line(self, order=None, quantity=50, **overrides):
        """Save one cohort line for the fixture's variety."""
        if order is not None:
            overrides['order'] = order
        return SalesOrderLine.objects.create(
            **self.line_values(quantity=quantity, **overrides),
        )

    def draw(self, line, cohort=None, quantity=50, revision=None):
        """Allocate one counted draw against a block, as a screen would."""
        cohort = cohort or self.cohort
        return allocate_targets(
            line, self.user,
            cohort_requests=[CohortRequest(
                cohort.pk, quantity,
                revision if revision is not None else cohort.revision,
            )],
        )

    def confirm(self, order):
        """Reserve a draft order's promises and return the reserved order."""
        return confirm_order(order, self.user)


class CohortLineTargetTests(CohortStockTestCase):
    """A cohort line promises a variety, exactly as a seedling line does."""

    def build(self, **overrides):
        """Build an unsaved cohort line so validation can be inspected."""
        return SalesOrderLine(**self.line_values(**overrides))

    def test_a_variety_is_what_a_cohort_line_names(self):
        """The customer is buying the crop, not the block it is counted in."""
        line = self.build()

        line.full_clean()
        line.save()

        self.assertEqual(line.variety_id, self.batch.variety_id)
        self.assertIsNone(line.item_id)

    def test_an_inventory_item_is_refused(self):
        """Naming a catalog item would say the stock has a tracking mode."""
        with self.assertRaises(ValidationError) as caught:
            self.build(variety=None).full_clean()

        self.assertIn('variety', caught.exception.message_dict)


class CohortAllocationTests(CohortStockTestCase):
    """A cohort allocation promises a count against one block."""

    def test_a_draw_reserves_a_quantity_rather_than_identities(self):
        """Nothing is promoted, so no plant record is created to sell."""
        line = self.cohort_line()

        allocations = self.draw(line)

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].quantity, 50)
        self.assertEqual(allocations[0].plant_cohort_id, self.cohort.pk)
        self.assertFalse(SpecificPlant.objects.filter(batch=self.batch).exists())

    def test_a_draw_is_measured_against_the_block_it_names(self):
        """A pending selection is tentative and does not hold stock yet."""
        line = self.cohort_line()
        self.draw(line)

        self.cohort.refresh_from_db()
        self.assertEqual(reserved_quantity(self.cohort), 0)
        self.assertEqual(available_quantity(self.cohort), 200)

    def test_confirmation_holds_the_count_away_from_everybody_else(self):
        """A reservation is what takes a count out of what can be promised."""
        line = self.cohort_line()
        self.draw(line)

        self.confirm(line.order)

        self.cohort.refresh_from_db()
        self.assertEqual(reserved_quantity(self.cohort), 50)
        self.assertEqual(available_quantity(self.cohort), 150)

    def test_two_orders_cannot_jointly_reserve_more_than_the_block_holds(self):
        """The second order is refused with what is actually free named."""
        cohort = self.available_cohort(quantity=60)
        first = self.cohort_line(quantity=50)
        self.draw(first, cohort=cohort)
        self.confirm(first.order)
        cohort.refresh_from_db()
        second = self.cohort_line(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            self.draw(second, cohort=cohort, quantity=50)

        self.assertIn('insufficient_stock', str(caught.exception))

    def test_a_count_written_down_below_its_reservations_is_refused(self):
        """A draft cannot be confirmed against stock that is no longer there."""
        cohort = self.available_cohort(quantity=60)
        line = self.cohort_line(quantity=50)
        self.draw(line, cohort=cohort)
        cohort.refresh_from_db()
        change_cohort(
            self.workspace, self.user,
            cohort_id=cohort.pk,
            expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS,
            idempotency_key=uuid4(),
            quantity=30,
            reason='Damping off.',
            loss_cause=CohortOperation.LossCause.FAILED,
        )

        with self.assertRaises(ValidationError) as caught:
            self.confirm(line.order)

        self.assertIn('only 30 is unpromised', str(caught.exception))

    def test_a_quarantined_block_cannot_be_allocated(self):
        """Health holds the stock back before a promise is ever made."""
        self.quarantine(self.cohort)
        line = self.cohort_line()

        with self.assertRaises(ValidationError) as caught:
            self.draw(line)

        self.assertIn('quarantined', str(caught.exception))

    def test_a_retained_block_cannot_be_allocated(self):
        """Stock kept for the nursery's own use was never on offer.

        Growing stock is a different answer and lives in `test_forward_sales`:
        a block still in plugs may be promised forward, and only a block whose
        plants are spoken for by the operation itself refuses the draw.
        """
        retained, _operation = change_cohort(
            self.workspace, self.user,
            cohort_id=self.observe(quantity=40).pk,
            expected_revision=1,
            action=CohortOperation.Action.RETAIN,
            idempotency_key=uuid4(),
            reason='Kept as stock plants.',
        )
        line = self.cohort_line(quantity=10)

        with self.assertRaises(ValidationError) as caught:
            self.draw(line, cohort=retained, quantity=10)

        self.assertIn('not_sellable', str(caught.exception))

    def test_a_block_of_another_variety_cannot_fill_the_line(self):
        """A cohort reaches its variety through its batch, as a plant does."""
        other = self.available_cohort(batch=make_production_batch())
        line = self.cohort_line()

        with self.assertRaises(ValidationError) as caught:
            self.draw(line, cohort=other)

        self.assertIn('wrong_variety', str(caught.exception))


class CohortPreviewTests(CohortStockTestCase):
    """The preview says what can be had before anything is promised."""

    def test_a_stale_revision_is_refused_rather_than_guessed_at(self):
        """The operator chose the count against a figure that has moved."""
        line = self.cohort_line()

        result = preview_targets(
            line, cohort_requests=[CohortRequest(self.cohort.pk, 50, self.cohort.revision + 1)],
        )

        self.assertEqual(result['selected'], [])
        self.assertEqual(result['conflicts'][0]['reason'], 'stale_revision')

    def test_one_basket_cannot_be_told_the_same_count_twice(self):
        """The second draw is answered against what the first already took."""
        cohort = self.available_cohort(quantity=60)
        line = self.cohort_line(quantity=100)

        result = preview_targets(line, cohort_requests=[
            CohortRequest(cohort.pk, 50, cohort.revision),
            CohortRequest(cohort.pk, 50, cohort.revision),
        ])

        self.assertEqual(len(result['selected']), 1)
        self.assertEqual(result['conflicts'][0]['reason'], 'insufficient_stock')
        self.assertEqual(result['conflicts'][0]['available'], '10')

    def test_a_selection_of_another_kind_is_refused_before_any_lock(self):
        """A plant offered to a cohort line is a mistake worth naming."""
        line = self.cohort_line()

        with self.assertRaises(ValidationError) as caught:
            preview_targets(line, plant_ids=[1])

        self.assertIn('cohort quantities only', str(caught.exception))


class CohortFulfillmentTests(CohortStockTestCase):
    """A count is quoted, reserved, dispatched and paid for as a count."""

    def dispatch(self, line):
        """Confirm one drawn order and dispatch its whole reservation."""
        order = self.confirm(line.order)
        allocation = line.allocations.get()
        return order, post_fulfillment(
            order, self.user,
            operation_key=uuid4(),
            allocation_ids=[allocation.pk],
        )

    def test_the_whole_cycle_runs_without_promoting_anything(self):
        """That is the point: no plant identity is created to sell a count."""
        line = self.cohort_line()
        self.draw(line)

        order, fulfillment = self.dispatch(line)
        record_payment(
            order, self.user,
            operation_key=uuid4(),
            paid_on=order.order_date,
            amount=order.total_incl_tax,
            method=Payment.Method.BANK_TRANSFER,
        )

        order.refresh_from_db()
        self.cohort.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.FULFILLED)
        self.assertEqual(self.cohort.quantity, 150)
        self.assertEqual(fulfillment.lines.get().allocation.quantity, 50)
        self.assertFalse(SpecificPlant.objects.filter(batch=self.batch).exists())

    def test_the_history_says_what_left_the_block(self):
        """A quantity that dropped with no operation explaining it is a hole."""
        line = self.cohort_line()
        self.draw(line)

        _order, fulfillment = self.dispatch(line)

        event = fulfillment.lines.get().cohort_event
        self.assertEqual(event.cohort_id, self.cohort.pk)
        self.assertEqual(event.operation.action, CohortOperation.Action.SOLD)
        self.assertEqual(event.quantity_before, 200)
        self.assertEqual(event.quantity_delta, -50)
        self.assertEqual(event.quantity_after, 150)

    def test_selling_the_last_of_a_block_depletes_it(self):
        """An empty block is depleted, exactly as any other emptying does."""
        cohort = self.available_cohort(quantity=50)
        line = self.cohort_line()
        self.draw(line, cohort=cohort)

        self.dispatch(line)

        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 0)
        self.assertEqual(cohort.lifecycle_state, PlantCohort.LifecycleState.DEPLETED)

    def test_reversing_a_dispatch_puts_the_count_back_where_it_was(self):
        """A reversal says the dispatch never happened, so nothing moved."""
        cohort = self.available_cohort(quantity=50)
        line = self.cohort_line()
        self.draw(line, cohort=cohort)
        _order, fulfillment = self.dispatch(line)

        reverse_fulfillment(
            fulfillment, self.user,
            operation_key=uuid4(), reason='Loaded the wrong trolley.',
        )

        cohort.refresh_from_db()
        self.assertEqual(cohort.quantity, 50)
        self.assertEqual(cohort.lifecycle_state, PlantCohort.LifecycleState.AVAILABLE)
        self.assertEqual(
            line.allocations.get().status, SalesOrderAllocation.Status.RESERVED,
        )


class CohortReturnTests(CohortStockTestCase):
    """A returned count lands in a block of its own, and says where from."""

    def setUp(self):
        super().setUp()
        self.line = self.cohort_line()
        self.draw(self.line)
        self.order = self.confirm(self.line.order)
        self.fulfillment = post_fulfillment(
            self.order, self.user,
            operation_key=uuid4(),
            allocation_ids=[self.line.allocations.get().pk],
        )
        self.dock = make_location(name='Returns dock', code='RETURNS-DOCK')

    def post_return(self, outcome, destination=None, **extra):
        """Return the whole dispatched count with an explicit outcome."""
        return post_return(
            self.order, self.user,
            operation_key=uuid4(),
            items=[{
                'fulfillment_line': self.fulfillment.lines.get(),
                'outcome': outcome,
                'destination': destination,
            }],
            reason='Customer changed the order.',
            **extra,
        )

    def test_a_returned_count_opens_a_new_block_linked_to_its_source(self):
        """Stock that has been to a customer is not the stock that stayed."""
        sales_return = self.post_return(
            SalesReturnLine.Outcome.AVAILABLE, destination=self.dock,
        )

        event = sales_return.lines.get().cohort_event
        returned = event.cohort
        self.cohort.refresh_from_db()
        self.assertNotEqual(returned.pk, self.cohort.pk)
        self.assertEqual(returned.quantity, 50)
        self.assertEqual(returned.location_id, self.dock.pk)
        self.assertEqual(returned.batch_id, self.cohort.batch_id)
        self.assertEqual(self.cohort.quantity, 150)
        self.assertEqual(
            list(event.source_cohorts.values_list('pk', flat=True)), [self.cohort.pk],
        )
        self.assertEqual(event.operation.action, CohortOperation.Action.RETURN)

    def test_a_discarded_return_is_taken_back_and_then_written_off(self):
        """Both facts are recorded, rather than the stock never coming back."""
        sales_return = self.post_return(SalesReturnLine.Outcome.DISCARDED)

        returned = sales_return.lines.get().cohort_event.cohort
        returned.refresh_from_db()
        actions = list(
            returned.events.order_by('pk').values_list('operation__action', flat=True),
        )
        self.assertEqual(actions, [CohortOperation.Action.RETURN, CohortOperation.Action.LOSS])
        self.assertEqual(returned.quantity, 0)
        self.assertEqual(
            returned.events.order_by('pk').last().operation.loss_cause,
            CohortOperation.LossCause.CULLED,
        )

    def test_a_quarantined_return_holds_only_what_came_back(self):
        """The block that never left is not condemned by what did."""
        sales_return = self.post_return(
            SalesReturnLine.Outcome.QUARANTINED,
            destination=self.dock,
            observation_type=HealthObservationType.objects.get(
                workspace=self.workspace, code='pest-signs',
            ),
            severity=HealthObservation.Severity.HIGH,
        )

        returned = sales_return.lines.get().cohort_event.cohort
        self.assertTrue(
            sales_return.quarantine_case.members.filter(cohort=returned).exists(),
        )
        self.assertFalse(
            sales_return.quarantine_case.members.filter(cohort=self.cohort).exists(),
        )

    def test_reversing_a_return_takes_the_count_back_out_again(self):
        """The plants are with the customer again, and the block says so."""
        sales_return = self.post_return(
            SalesReturnLine.Outcome.AVAILABLE, destination=self.dock,
        )
        returned = sales_return.lines.get().cohort_event.cohort

        reverse_return(
            sales_return, self.user,
            operation_key=uuid4(), reason='Recorded against the wrong order.',
        )

        returned.refresh_from_db()
        self.assertEqual(returned.quantity, 0)
        self.assertEqual(
            self.line.allocations.get().status, SalesOrderAllocation.Status.FULFILLED,
        )

    def test_a_returned_block_that_moved_on_cannot_be_reversed(self):
        """There is nothing left in it to take away a second time."""
        sales_return = self.post_return(
            SalesReturnLine.Outcome.AVAILABLE, destination=self.dock,
        )
        returned = sales_return.lines.get().cohort_event.cohort
        returned.refresh_from_db()
        promote_cohort(
            self.workspace, self.user,
            cohort_id=returned.pk,
            expected_revision=returned.revision,
            quantity=50,
            idempotency_key=uuid4(),
            reason='Number the returned stock for a specimen order.',
        )

        with self.assertRaises(ValidationError) as caught:
            reverse_return(
                sales_return, self.user,
                operation_key=uuid4(), reason='Recorded against the wrong order.',
            )

        self.assertIn('already been reallocated', str(caught.exception))
