"""Selling nursery stock months before it is ready to leave.

Spring orders are placed in winter, against plants that are still in plugs. The
commitment and the dispatch are separate questions asked at separate times, so
these cover promising stock that is only growing, refusing to ship it until
somebody grades it ready, and recording the commercial outcome when the plants
never arrive at all.
"""

from uuid import uuid4

from django.core.exceptions import ValidationError

from plantings.cohort_availability import available_quantity, reserved_quantity
from plantings.cohorts import change_cohort
from plantings.models import CohortOperation, PlantCohort

from .commerce import (
    order_commerce_summary,
    post_fulfillment,
    record_shortfall,
)
from .models import (
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderShortfall,
)
from .test_cohort_lines import CohortStockTestCase


class ForwardStockTestCase(CohortStockTestCase):
    """A nursery whose next crop is sold before anybody has graded it."""

    def growing_cohort(self, quantity=200):
        """Observe a block nobody has graded ready, as a sowing leaves one."""
        return self.observe(quantity=quantity)

    def committed(self, quantity=50, cohort=None):
        """Promise a count of growing stock on a confirmed order."""
        cohort = cohort or self.growing_cohort()
        line = self.cohort_line(quantity=quantity)
        allocations = self.draw(line, cohort=cohort, quantity=quantity)
        self.confirm(line.order)
        cohort.refresh_from_db()
        return line, cohort, allocations[0]

    def dispatch(self, order, allocation):
        """Post one dispatch of a promised count."""
        return post_fulfillment(
            order, self.user,
            operation_key=uuid4(), allocation_ids=[allocation.pk],
        )


class ForwardCommitmentTests(ForwardStockTestCase):
    """A block still in plugs can be sold, and holds the stock while it grows."""

    def test_growing_stock_can_be_promised_to_an_order(self):
        """The commitment is what a winter order for spring plants is."""
        line, cohort, allocation = self.committed()

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)
        self.assertEqual(cohort.lifecycle_state, PlantCohort.LifecycleState.GROWING)
        self.assertEqual(
            SalesOrder.objects.get(pk=line.order.pk).status,
            SalesOrder.Status.CONFIRMED,
        )

    def test_a_forward_commitment_holds_the_stock_away_from_everybody_else(self):
        """Promising growing stock is what stops it being promised twice."""
        _line, cohort, _allocation = self.committed(quantity=50)

        self.assertEqual(reserved_quantity(cohort), 50)
        self.assertEqual(available_quantity(cohort), 150)

    def test_a_block_cannot_be_committed_past_the_count_that_exists(self):
        """Two winter orders cannot both be promised the same spring plants."""
        cohort = self.growing_cohort(quantity=60)
        self.committed(quantity=50, cohort=cohort)
        second = self.cohort_line(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            self.draw(second, cohort=cohort, quantity=50)

        self.assertIn('insufficient_stock', str(caught.exception))

    def test_the_order_reports_what_it_holds_that_is_not_ready(self):
        """A salesperson answering "when?" needs the two figures apart."""
        line, _cohort, _allocation = self.committed(quantity=50)

        summary = order_commerce_summary(line.order)

        self.assertEqual(summary['reserved_quantity'], 50)
        self.assertEqual(summary['committed_forward_quantity'], 50)
        self.assertEqual(summary['short_quantity'], 0)

    def test_grading_the_block_ready_leaves_the_commitment_alone(self):
        """The promise survives the readiness fact, which is all that changed."""
        line, cohort, allocation = self.committed(quantity=50)

        self.make_available(cohort)

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)
        self.assertEqual(
            order_commerce_summary(line.order)['committed_forward_quantity'], 0,
        )


class ForwardDispatchTests(ForwardStockTestCase):
    """Committing is not shipping: the plants have to actually be ready."""

    def test_growing_stock_cannot_be_dispatched(self):
        """The plants are sold, but they are not on a trolley yet."""
        line, _cohort, allocation = self.committed(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            self.dispatch(line.order, allocation)

        self.assertIn('Not ready to dispatch', str(caught.exception))

    def test_the_refusal_names_every_block_that_is_not_ready(self):
        """One picking round should not need six attempts to learn six answers."""
        first = self.growing_cohort(quantity=60)
        second = self.growing_cohort(quantity=60)
        line = self.cohort_line(quantity=100)
        allocations = self.draw(line, cohort=first, quantity=50)
        allocations += self.draw(line, cohort=second, quantity=50)
        self.confirm(line.order)

        with self.assertRaises(ValidationError) as caught:
            post_fulfillment(
                line.order, self.user, operation_key=uuid4(),
                allocation_ids=[row.pk for row in allocations],
            )

        message = str(caught.exception)
        self.assertIn(str(first.pk), message)
        self.assertIn(str(second.pk), message)

    def test_marking_the_stock_ready_lets_the_commitment_ship_unchanged(self):
        """Nothing is re-entered: the promise made in winter is the one that goes."""
        line, cohort, allocation = self.committed(quantity=50)
        self.make_available(cohort)

        fulfillment = self.dispatch(line.order, allocation)

        allocation.refresh_from_db()
        cohort.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.FULFILLED)
        self.assertEqual(cohort.quantity, 150)
        self.assertEqual(fulfillment.lines.get().allocation_id, allocation.pk)
        self.assertEqual(
            SalesOrder.objects.get(pk=line.order.pk).status,
            SalesOrder.Status.FULFILLED,
        )

    def test_a_ready_block_still_refuses_to_ship_once_it_is_quarantined(self):
        """Readiness is not the only reason stock stays where it is."""
        line, cohort, allocation = self.committed(quantity=50)
        self.make_available(cohort)
        self.quarantine(cohort)

        with self.assertRaises(ValidationError) as caught:
            self.dispatch(line.order, allocation)

        self.assertIn('quarantine', str(caught.exception).lower())


class ShortfallTests(ForwardStockTestCase):
    """A commitment the stock never grew into is a commercial outcome."""

    def lose(self, cohort, quantity):
        """Write a count off the block, as a failed crop does."""
        cohort.refresh_from_db()
        cohort, _operation = change_cohort(
            self.workspace, self.user,
            cohort_id=cohort.pk, expected_revision=cohort.revision,
            action=CohortOperation.Action.LOSS, idempotency_key=uuid4(),
            quantity=quantity, reason='Damping off.',
            loss_cause=CohortOperation.LossCause.FAILED,
        )
        return cohort

    def test_part_of_a_commitment_is_given_up_and_the_rest_kept(self):
        """The kept half never falls back into availability on the way past."""
        line, cohort, allocation = self.committed(quantity=50)
        self.lose(cohort, 10)

        shortfall = record_shortfall(
            line.order, self.user, allocation_id=allocation.pk,
            quantity=10, reason='Damping off took the last flat.',
        )

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.SHORTFALL)
        self.assertEqual(shortfall.quantity, 10)
        self.assertEqual(shortfall.replacement.quantity, 40)
        self.assertEqual(
            shortfall.replacement.status, SalesOrderAllocation.Status.RESERVED,
        )
        self.assertEqual(shortfall.replacement.plant_cohort_id, cohort.pk)
        self.assertEqual(reserved_quantity(cohort), 40)

    def test_a_shortfall_requires_a_stated_reason(self):
        """A commitment that quietly shrank is one nobody can explain later."""
        line, _cohort, allocation = self.committed(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            record_shortfall(
                line.order, self.user, allocation_id=allocation.pk,
                quantity=10, reason='   ',
            )

        self.assertIn('reason', caught.exception.message_dict)

    def test_the_short_part_is_no_longer_owed_by_the_order(self):
        """Shipping what is left completes the order rather than stalling it."""
        line, cohort, allocation = self.committed(quantity=50)
        cohort = self.lose(cohort, 10)
        shortfall = record_shortfall(
            line.order, self.user, allocation_id=allocation.pk,
            quantity=10, reason='Damping off took the last flat.',
        )
        self.make_available(cohort)

        self.dispatch(line.order, shortfall.replacement)

        order = SalesOrder.objects.get(pk=line.order.pk)
        self.assertEqual(order.status, SalesOrder.Status.FULFILLED)
        summary = order_commerce_summary(order)
        self.assertEqual(summary['short_quantity'], 10)
        self.assertEqual(summary['fulfilled_quantity'], 40)

    def test_a_shortfall_appears_in_the_reservation_history_with_its_reason(self):
        """The customer's explanation lives on the promise it was given about."""
        line, cohort, allocation = self.committed(quantity=50)
        self.lose(cohort, 10)

        record_shortfall(
            line.order, self.user, allocation_id=allocation.pk,
            quantity=10, reason='Damping off took the last flat.',
        )

        event = allocation.events.get(
            event_type=ReservationEvent.EventType.SHORTFALL,
        )
        self.assertEqual(event.reason, 'Damping off took the last flat.')

    def test_a_shortfall_cannot_exceed_the_promise_it_closes(self):
        """Giving up more than was promised would owe the customer a negative."""
        line, _cohort, allocation = self.committed(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            record_shortfall(
                line.order, self.user, allocation_id=allocation.pk,
                quantity=51, reason='Damping off.',
            )

        self.assertIn('quantity', caught.exception.message_dict)

    def test_an_order_that_would_supply_nothing_is_sent_to_cancellation(self):
        """"Fulfilled" would be a plainly false word for a load that never left."""
        line, _cohort, allocation = self.committed(quantity=50)

        with self.assertRaises(ValidationError) as caught:
            record_shortfall(
                line.order, self.user, allocation_id=allocation.pk,
                quantity=50, reason='The whole crop failed.',
            )

        self.assertIn('cancel', str(caught.exception).lower())
        self.assertFalse(SalesOrderShortfall.objects.exists())

    def test_a_whole_promise_is_given_up_once_something_else_shipped(self):
        """A failed line does not hold a part-delivered order open for ever."""
        cohort = self.growing_cohort(quantity=200)
        line = self.cohort_line(quantity=100)
        first = self.draw(line, cohort=cohort, quantity=50)[0]
        second = self.draw(line, cohort=cohort, quantity=50)[0]
        self.confirm(line.order)
        self.make_available(cohort)
        self.dispatch(line.order, first)

        record_shortfall(
            line.order, self.user, allocation_id=second.pk,
            quantity=50, reason='The second half never sized up.',
        )

        second.refresh_from_db()
        order = SalesOrder.objects.get(pk=line.order.pk)
        self.assertEqual(second.status, SalesOrderAllocation.Status.SHORTFALL)
        self.assertEqual(order.status, SalesOrder.Status.FULFILLED)

    def test_a_draft_order_has_no_commitment_to_be_short_of(self):
        """Nothing was promised yet, so the selection is simply removed."""
        line = self.cohort_line(quantity=50)
        allocation = self.draw(line, cohort=self.growing_cohort(), quantity=50)[0]

        with self.assertRaises(ValidationError) as caught:
            record_shortfall(
                line.order, self.user, allocation_id=allocation.pk,
                quantity=10, reason='Damping off.',
            )

        self.assertIn('status', caught.exception.message_dict)
