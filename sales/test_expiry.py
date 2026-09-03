"""Contracts for the scheduled release of lapsed sales reservations."""

# pylint: disable=duplicate-code,missing-function-docstring

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from plantings.register import RegisterFilters, register_queryset
from tests.factories import make_seed_tray
from work.models import WorkTaskLink, WorkTaskRule, WorkTaskType
from work.projections import projected_tasks
from work.services import acknowledge_projection

from .expiry import SWEEP_REASON, due_reservations, expire_due_reservations
from .models import (
    ReservationEvent,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLine,
)
from .services import LotRequest, allocate_targets, confirm_order, preview_targets
from .test_commerce import CommerceFixtureTestCase
from .test_counted_lines import CountedStockTestCase


class ReservationExpirySweepTests(CommerceFixtureTestCase):
    """A hold past its expiry frees its stock without anyone noticing it."""

    def held_order(self, expires_at):
        """Confirm one order holding one plant until the given instant."""
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        SalesOrderAllocation.objects.filter(
            pk=allocations[0]['pk'],
        ).update(expires_at=expires_at)
        return order, plant, SalesOrderAllocation.objects.get(pk=allocations[0]['pk'])

    def test_a_due_hold_is_expired_exactly_once_and_re_runs_change_nothing(self):
        past = timezone.now() - timedelta(hours=1)
        _order, _plant, allocation = self.held_order(past)

        expired = expire_due_reservations(self.workspace)

        self.assertEqual([row.pk for row in expired], [allocation.pk])
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.EXPIRED)

        self.assertEqual(expire_due_reservations(self.workspace), [])
        self.assertEqual(
            allocation.events.filter(
                event_type=ReservationEvent.EventType.EXPIRED,
            ).count(),
            1,
        )

    def test_an_open_ended_or_future_hold_is_left_alone(self):
        _open_order, _open_plant, open_ended = self.held_order(None)
        _future_order, _future_plant, future = self.held_order(
            timezone.now() + timedelta(days=3),
        )

        self.assertEqual(expire_due_reservations(self.workspace), [])

        for allocation in (open_ended, future):
            allocation.refresh_from_db()
            self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)
        self.assertEqual(list(due_reservations(self.workspace)), [])

    def test_released_stock_is_immediately_allocatable_to_another_order(self):
        past = timezone.now() - timedelta(minutes=5)
        _order, plant, _allocation = self.held_order(past)

        response = self.client.post(self.orders_url, {'status': 'draft'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        second = response.data
        response = self.client.post(self.lines_url, {
            'order': second['pk'], 'line_type': 'seedling',
            'variety': plant.batch.variety_id, 'description': 'Replacement',
            'quantity': 1, 'unit_price': '10.0000', 'tax_rate': '15.0000',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        line = response.data

        blocked = self.client.post(
            f"{self.orders_url}{second['pk']}/allocate/",
            {'line': line['pk'], 'plant_ids': [plant.pk]}, format='json',
        )
        self.assertEqual(blocked.status_code, 400, blocked.data)

        expire_due_reservations(self.workspace)

        allowed = self.client.post(
            f"{self.orders_url}{second['pk']}/allocate/",
            {'line': line['pk'], 'plant_ids': [plant.pk]}, format='json',
        )
        self.assertEqual(allowed.status_code, 201, allowed.data)

    def test_the_history_records_why_each_hold_ended(self):
        past = timezone.now() - timedelta(hours=2)
        _order, _plant, allocation = self.held_order(past)

        expire_due_reservations(self.workspace)

        event = allocation.events.get(
            event_type=ReservationEvent.EventType.EXPIRED,
        )
        self.assertEqual(event.reason, SWEEP_REASON)
        self.assertIsNone(event.created_by)
        self.assertEqual(
            [row.event_type for row in allocation.events.all()],
            [ReservationEvent.EventType.RESERVED, ReservationEvent.EventType.EXPIRED],
        )

    def test_a_tray_unit_hold_lapses_and_returns_to_available(self):
        tray = make_seed_tray(workspace=self.workspace)
        response = self.client.post(self.orders_url, {'status': 'draft'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        order = response.data
        response = self.client.post(self.lines_url, {
            'order': order['pk'], 'line_type': 'unit',
            'item': tray.inventory_unit.item_id, 'description': 'One tray',
            'quantity': 1, 'unit_price': '5.0000', 'tax_rate': '15.0000',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        line_pk = response.data['pk']
        allocated = self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': line_pk, 'unit_ids': [tray.inventory_unit_id]}, format='json',
        )
        self.assertEqual(allocated.status_code, 201, allocated.data)
        confirmed = self.client.post(
            f"{self.orders_url}{order['pk']}/confirm/", {}, format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        SalesOrderAllocation.objects.filter(pk=allocated.data[0]['pk']).update(
            expires_at=timezone.now() - timedelta(days=1),
        )

        expired = expire_due_reservations(self.workspace)

        self.assertEqual(len(expired), 1)

        line = SalesOrderLine.objects.get(pk=line_pk)
        self.assertEqual(
            preview_targets(line, unit_ids=[tray.inventory_unit_id])['selected'],
            [tray.inventory_unit_id],
        )

    def test_the_register_reports_when_a_live_hold_lapses(self):
        expiry = timezone.now() + timedelta(days=2)
        _order, plant, _allocation = self.held_order(expiry)

        row = register_queryset(self.workspace, RegisterFilters(reserved=True)).get(pk=plant.pk)

        self.assertEqual(row.reserved_until, expiry)

    def test_a_hold_on_a_cancelled_order_never_reaches_the_sweep(self):
        past = timezone.now() - timedelta(hours=1)
        order, _plant, allocation = self.held_order(past)
        cancelled = self.client.post(
            f"{self.orders_url}{order['pk']}/cancel/",
            {'reason': 'Customer withdrew.'}, format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)

        self.assertEqual(expire_due_reservations(self.workspace), [])

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RELEASED)


class ExpireReservationsCommandTests(CommerceFixtureTestCase):
    """The scheduled entry point reports what it did and stays a dry run."""

    def due_allocation(self):
        plant = self.available_plant()
        _order, allocations = self.confirmed_order([plant])
        SalesOrderAllocation.objects.filter(pk=allocations[0]['pk']).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        return SalesOrderAllocation.objects.get(pk=allocations[0]['pk'])

    def run_command(self, *arguments):
        output = StringIO()
        call_command('expire_reservations', *arguments, stdout=output)
        return output.getvalue()

    def test_a_dry_run_names_the_due_holds_and_changes_nothing(self):
        allocation = self.due_allocation()

        output = self.run_command('--dry-run')

        self.assertIn('SO-000001', output)
        self.assertIn('1 reservation(s) due for expiry.', output)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.RESERVED)

    def test_the_sweep_expires_the_due_holds_and_reports_the_count(self):
        allocation = self.due_allocation()

        output = self.run_command()

        self.assertIn('Expired 1 reservation(s).', output)
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, SalesOrderAllocation.Status.EXPIRED)
        self.assertIn('Expired 0 reservation(s).', self.run_command())


class ReservationExpiryProjectionTests(CommerceFixtureTestCase):
    """The queue warns before a hold lapses and follows up after it."""

    def setUp(self):
        super().setUp()
        self.rule = WorkTaskRule.objects.create(
            workspace=self.workspace, code='reservation-expiry',
            name='Sales reservations lapsing',
            task_type=WorkTaskType.RESERVATION,
            trigger=WorkTaskRule.Trigger.RESERVATION_EXPIRY,
            due_start_offset_days=-2,
        )

    def keys(self):
        return [task.key for task in projected_tasks(self.workspace)]

    def test_a_live_hold_projects_a_warning_naming_its_order_and_plants(self):
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        SalesOrderAllocation.objects.filter(pk=allocations[0]['pk']).update(
            expires_at=timezone.now() + timedelta(days=1),
        )

        task = next(
            row for row in projected_tasks(self.workspace)
            if row.key == f'rule:{self.rule.pk}:reservation:{order["pk"]}'
        )

        self.assertIn(order['order_number'], task.title)
        self.assertIn(plant, [link.target for link in task.targets])
        self.assertEqual(task.source_snapshot['held_count'], 1)
        self.assertEqual(task.due_end - task.due_start, timedelta(days=2))

    def test_a_hold_with_no_expiry_projects_nothing(self):
        plant = self.available_plant()
        self.confirmed_order([plant])

        self.assertEqual(self.keys(), [])

    def test_a_lapsed_hold_projects_the_reallocation_it_left_behind(self):
        plant = self.available_plant()
        order, allocations = self.confirmed_order([plant])
        SalesOrderAllocation.objects.filter(pk=allocations[0]['pk']).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        expire_due_reservations(self.workspace)

        self.assertEqual(
            self.keys(), [f'rule:{self.rule.pk}:reservation-lapsed:{order["pk"]}'],
        )

        replacement = self.available_plant(
            batch=plant.batch, cell_planting=plant.cell_planting,
        )
        line = SalesOrder.objects.get(pk=order['pk']).lines.get()
        allocate_targets(line, self.user, plant_ids=[replacement.pk])

        self.assertEqual(self.keys(), [])


class CountedReservationProjectionTests(CountedStockTestCase):
    """A counted hold reaches the queue as pots, not as one anonymous row.

    Nothing about a lapse is identity-specific, so the projection has to read
    a counted hold as readily as it reads a plant. It did not: it counted
    rows, and it built its target by falling through to the numbered-unit
    branch, which for anonymous stock points at nothing at all.
    """

    def setUp(self):
        super().setUp()
        self.rule = WorkTaskRule.objects.create(
            workspace=self.workspace, code='reservation-expiry',
            name='Sales reservations lapsing',
            task_type=WorkTaskType.RESERVATION,
            trigger=WorkTaskRule.Trigger.RESERVATION_EXPIRY,
            due_start_offset_days=-2,
        )

    def held_pots(self, quantity=50, expires_in=timedelta(days=1)):
        """Confirm one order holding a counted draw until the given offset."""
        lot = self.receive()
        line = self.counted_line(quantity=quantity)
        allocation = allocate_targets(line, self.user, lot_requests=[
            LotRequest(lot.pk, self.store.pk, quantity),
        ])[0]
        confirm_order(line.order, self.user)
        SalesOrderAllocation.objects.filter(pk=allocation.pk).update(
            expires_at=timezone.now() + expires_in,
        )
        return line, lot

    def reservation_task(self, line):
        """Return the warning projected for one order's live counted hold."""
        return next(
            row for row in projected_tasks(self.workspace)
            if row.key == f'rule:{self.rule.pk}:reservation:{line.order.pk}'
        )

    def test_a_counted_hold_is_reported_as_the_pots_it_keeps_back(self):
        """Fifty pots held is fifty, not the one row that recorded them."""
        line, _ = self.held_pots(quantity=50)

        task = self.reservation_task(line)

        self.assertEqual(task.source_snapshot['held_count'], 50)

    def test_a_counted_hold_names_the_lot_it_is_standing_in(self):
        """Anonymous stock has no identity, so the lot is what to point at."""
        line, lot = self.held_pots(quantity=50)

        task = self.reservation_task(line)

        self.assertIn(lot, [link.target for link in task.targets])
        self.assertIn(
            f'50 × {self.item.name}', [link.label for link in task.targets],
        )

    def test_a_counted_warning_can_be_acknowledged(self):
        """A target with no content type failed the moment anyone took it on."""
        line, _ = self.held_pots(quantity=50)

        task = acknowledge_projection(
            self.workspace, self.user, self.reservation_task(line).key,
        )

        self.assertEqual(
            task.links.filter(role=WorkTaskLink.Role.TARGET).count(), 2,
        )

    def test_a_covered_counted_line_is_not_owed_more_stock(self):
        """One allocation of fifty covers a line of fifty, so nothing lapsed.

        Counting rows read this as one held against fifty requested, and the
        order projected a reallocation it did not need for as long as it
        stayed open.
        """
        line, _ = self.held_pots(quantity=50, expires_in=-timedelta(hours=1))
        expire_due_reservations(self.workspace)
        replacement = self.receive()
        allocate_targets(line, self.user, lot_requests=[
            LotRequest(replacement.pk, self.store.pk, 50),
        ])

        self.assertEqual(
            [row.key for row in projected_tasks(self.workspace)], [],
        )
