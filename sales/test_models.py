"""Model and arithmetic contracts for customer sales."""

import random
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from workspaces.models import get_current_workspace

from . import calculations
from .calculations import (
    distribute_money,
    line_position_amounts,
    money,
    proportional_refund,
)
from .models import Customer, SalesOrder, SalesOrderLine
from .services import create_order, update_pricing_mode


class SalesOrderArithmeticTests(TestCase):
    """Entered commercial terms produce stable canonical money snapshots."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='sales-model-user')

    def make_line(self, order, **overrides):
        """Build an arithmetic-only line with adjustable entered terms."""
        values = {
            'order': order,
            'line_type': SalesOrderLine.LineType.TRAY,
            'description': 'Two propagation trays',
            'quantity': 2,
            'unit_price': Decimal('11.5000'),
            'tax_rate': Decimal('15'),
            'discount_type': SalesOrderLine.DiscountType.FIXED,
            'discount_value': Decimal('3'),
        }
        # Arithmetic does not need a catalog target, so bypass only the target
        # constraint here by creating a seedling-shaped unsaved test double.
        line = SalesOrderLine(**values)
        for field, value in overrides.items():
            setattr(line, field, value)
        return line

    def test_order_creation_snapshots_workspace_defaults_and_sequences_numbers(self):
        """New documents freeze defaults and receive readable unique numbers."""
        self.workspace.sales_prices_include_tax = True
        self.workspace.save()
        first = create_order(self.workspace, self.user, status=SalesOrder.Status.QUOTE)
        second = create_order(self.workspace, self.user)
        self.assertEqual(first.order_number, 'SO-000001')
        self.assertEqual(second.order_number, 'SO-000002')
        self.assertTrue(first.prices_include_tax)
        self.assertEqual(first.currency_code, 'NZD')
        self.assertIsNotNone(first.quote_date)
        self.assertIsNotNone(second.order_date)

    def test_exclusive_and_inclusive_calculations_reconcile(self):
        """Both entry modes return canonical components that add exactly."""
        from .calculations import calculate_line  # pylint: disable=import-outside-toplevel

        exclusive = create_order(self.workspace, self.user)
        amounts = calculate_line(self.make_line(exclusive))
        self.assertEqual(amounts.subtotal_ex_tax, Decimal('20.0000'))
        self.assertEqual(amounts.tax_total, Decimal('3.0000'))
        self.assertEqual(amounts.total_incl_tax, Decimal('23.0000'))

        inclusive = create_order(self.workspace, self.user, prices_include_tax=True)
        amounts = calculate_line(self.make_line(inclusive))
        self.assertEqual(amounts.subtotal_ex_tax, Decimal('17.3913'))
        self.assertEqual(amounts.tax_total, Decimal('2.6087'))
        self.assertEqual(amounts.total_incl_tax, Decimal('20.0000'))
        self.assertEqual(amounts.subtotal_ex_tax + amounts.tax_total, amounts.total_incl_tax)

    def test_percentage_discount_uses_entered_gross(self):
        """Percentage discounts apply before tax in either entry mode."""
        from .calculations import calculate_line  # pylint: disable=import-outside-toplevel

        order = create_order(self.workspace, self.user)
        line = self.make_line(
            order,
            discount_type=SalesOrderLine.DiscountType.PERCENTAGE,
            discount_value=Decimal('10'),
        )
        amounts = calculate_line(line)
        self.assertEqual(amounts.discount_ex_tax, Decimal('2.3000'))
        self.assertEqual(amounts.total_incl_tax, Decimal('23.8050'))

    def test_customer_deactivates_instead_of_deleting(self):
        """Historical customer identities survive deactivation."""
        customer = Customer.objects.create(workspace=self.workspace, name='Local gardener')
        with self.assertRaisesMessage(ValidationError, 'deactivated'):
            customer.delete()
        customer.active = False
        customer.save()
        self.assertFalse(customer.active)

    def test_confirmed_pricing_mode_is_immutable(self):
        """A confirmed order cannot reinterpret its snapshotted terms."""
        order = create_order(self.workspace, self.user)
        SalesOrder.objects.filter(pk=order.pk).update(status=SalesOrder.Status.CONFIRMED)
        order.refresh_from_db()
        with self.assertRaises(ValidationError):
            update_pricing_mode(order, True)

    def test_position_amounts_reconcile_rounding_to_the_line(self):
        """The final exact item carries only the deterministic remainder."""
        line = SimpleNamespace(
            quantity=3,
            gross_ex_tax=Decimal('10'),
            discount_ex_tax=Decimal('1'),
            subtotal_ex_tax=Decimal('9'),
            tax_total=Decimal('1.35'),
            total_incl_tax=Decimal('10.35'),
        )
        positions = line_position_amounts(line)
        for field in (
                'gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax',
                'tax_total', 'total_incl_tax'):
            self.assertEqual(
                sum(position[field] for position in positions.values()),
                getattr(line, field),
            )

    def test_partial_refund_preserves_source_tax_and_discount_ratios(self):
        """A value correction remains tied to original recognized terms."""
        line = SimpleNamespace(
            pk=1,
            gross_ex_tax=Decimal('10.0000'),
            discount_ex_tax=Decimal('1.0000'),
            subtotal_ex_tax=Decimal('9.0000'),
            tax_total=Decimal('1.3500'),
            total_incl_tax=Decimal('10.3500'),
        )
        result = proportional_refund(Decimal('5.1750'), [{
            'line': line,
            'remaining_total': Decimal('10.3500'),
        }])[0]
        self.assertEqual(result['gross_ex_tax'], Decimal('5.0000'))
        self.assertEqual(result['discount_ex_tax'], Decimal('0.5000'))
        self.assertEqual(result['subtotal_ex_tax'], Decimal('4.5000'))
        self.assertEqual(result['tax_total'], Decimal('0.6750'))


class RefundAllocationTests(SimpleTestCase):
    """An inclusive refund splits exactly and never overdraws one line."""

    def make_source(self, pk, total, *, tax_rate=Decimal('15')):
        """Build a fulfilled line whose components add to a given total."""
        subtotal = money(total / (Decimal('1') + tax_rate / Decimal('100')))
        return SimpleNamespace(
            pk=pk,
            gross_ex_tax=money(subtotal * Decimal('1.1')),
            discount_ex_tax=money(subtotal * Decimal('0.1')),
            subtotal_ex_tax=subtotal,
            tax_total=money(total - subtotal),
            total_incl_tax=money(total),
        )

    def available(self, totals, remaining=None):
        """Offer lines for refund, optionally with value already refunded."""
        remaining = totals if remaining is None else remaining
        return [
            {'line': self.make_source(index, total), 'remaining_total': money(left)}
            for index, (total, left) in enumerate(zip(totals, remaining), start=1)
        ]

    def assert_allocation_is_sound(self, amount, rows, shares):
        """Every allocation adds back exactly and stays inside each line."""
        self.assertEqual(
            money(sum(share['total_incl_tax'] for share in shares)), money(amount),
        )
        for row, share in zip(rows, shares):
            self.assertIs(share['line'], row['line'])
            self.assertGreaterEqual(share['total_incl_tax'], Decimal('0'))
            self.assertLessEqual(share['total_incl_tax'], row['remaining_total'])
            self.assertEqual(
                share['subtotal_ex_tax'] + share['tax_total'],
                share['total_incl_tax'],
            )
            self.assertEqual(
                share['gross_ex_tax'] - share['discount_ex_tax'],
                share['subtotal_ex_tax'],
            )

    def test_two_lines_split_in_proportion_to_their_remaining_value(self):
        """A refund across two lines follows the values still refundable."""
        rows = self.available([Decimal('30.0000'), Decimal('10.0000')])
        shares = proportional_refund(Decimal('20.0000'), rows)
        self.assertEqual(
            [share['total_incl_tax'] for share in shares],
            [Decimal('15.0000'), Decimal('5.0000')],
        )
        self.assert_allocation_is_sound(Decimal('20.0000'), rows, shares)

    def test_ten_lines_absorb_an_indivisible_amount_without_losing_a_part(self):
        """A total that cannot divide evenly still adds back exactly."""
        rows = self.available([Decimal('1.0000')] * 10)
        shares = proportional_refund(Decimal('0.3333'), rows)
        self.assert_allocation_is_sound(Decimal('0.3333'), rows, shares)

    def test_refunding_the_whole_available_total_returns_each_line_entire(self):
        """A full refund hands every line back exactly what remained on it."""
        totals = [Decimal('11.3300'), Decimal('7.7700'), Decimal('0.0100')]
        rows = self.available(totals)
        amount = money(sum(totals))
        shares = proportional_refund(amount, rows)
        self.assertEqual([share['total_incl_tax'] for share in shares], totals)
        self.assert_allocation_is_sound(amount, rows, shares)

    def test_a_residue_left_by_an_earlier_refund_is_never_overdrawn(self):
        """Rounding drift lands on a line with room, not on a spent one.

        Allocating 344.1157 proportionally rounds four of these lines down,
        and the residual quantum used to be handed to the last line by
        construction — which had 0.0040 left and would go negative.
        """
        totals = [
            Decimal('183.0326'), Decimal('24.9388'), Decimal('9.2674'),
            Decimal('126.8753'), Decimal('40.0000'),
        ]
        remaining = totals[:4] + [Decimal('0.0040')]
        rows = self.available(totals, remaining)
        amount = Decimal('344.1157')
        shares = proportional_refund(amount, rows)
        self.assertEqual(shares[-1]['total_incl_tax'], Decimal('0.0040'))
        self.assert_allocation_is_sound(amount, rows, shares)

    def test_a_fully_refunded_line_receives_nothing_more(self):
        """An exhausted balance takes no share of a later refund."""
        rows = self.available(
            [Decimal('20.0000'), Decimal('20.0000')],
            [Decimal('0.0000'), Decimal('20.0000')],
        )
        shares = proportional_refund(Decimal('12.0000'), rows)
        self.assertEqual(shares[0]['total_incl_tax'], Decimal('0.0000'))
        self.assertEqual(shares[1]['total_incl_tax'], Decimal('12.0000'))
        self.assert_allocation_is_sound(Decimal('12.0000'), rows, shares)

    def test_a_line_given_away_at_full_discount_allocates_no_components(self):
        """A zero-value line has no ratios to preserve and no share to take."""
        free = SimpleNamespace(
            pk=1, gross_ex_tax=Decimal('10.0000'),
            discount_ex_tax=Decimal('10.0000'), subtotal_ex_tax=Decimal('0.0000'),
            tax_total=Decimal('0.0000'), total_incl_tax=Decimal('0.0000'),
        )
        rows = [
            {'line': free, 'remaining_total': Decimal('0.0000')},
            {'line': self.make_source(2, Decimal('23.0000')),
             'remaining_total': Decimal('23.0000')},
        ]
        shares = proportional_refund(Decimal('23.0000'), rows)
        self.assertEqual(shares[0]['total_incl_tax'], Decimal('0.0000'))
        self.assertEqual(shares[0]['gross_ex_tax'], Decimal('0.0000'))
        self.assert_allocation_is_sound(Decimal('23.0000'), rows, shares)

    def test_a_refund_beyond_the_available_total_is_refused(self):
        """The guard rejects what no selected line can fund."""
        rows = self.available([Decimal('5.0000')])
        for amount in (Decimal('0'), Decimal('-1.0000'), Decimal('5.0001')):
            with self.assertRaises(ValueError):
                proportional_refund(amount, rows)

    def test_allocation_is_exact_over_generated_amounts_and_line_shapes(self):
        """The parts always sum back to the source, whatever the shape.

        Density of cases matters more than any one example here: the drift
        that overdraws a line only appears at particular combinations of
        magnitude, line count, and requested amount.
        """
        generator = random.Random(20260828)
        for case in range(2000):
            count = generator.randint(1, 8)
            scale = generator.choice([50, 10_000, 20_000_000])
            totals = [
                money(Decimal(generator.randint(1, scale)) / 10_000)
                for _ in range(count)
            ]
            remaining = [
                money(total * Decimal(generator.randint(0, 100)) / 100)
                for total in totals
            ]
            available_total = money(sum(remaining))
            if available_total <= 0:
                continue
            amount = money(
                Decimal(generator.randint(1, int(available_total * 10_000))) / 10_000
            )
            rows = [
                {'line': self.make_source(index, total), 'remaining_total': left}
                for index, (total, left) in enumerate(zip(totals, remaining), start=1)
            ]
            with self.subTest(case=case, amount=amount):
                self.assert_allocation_is_sound(
                    amount, rows, proportional_refund(amount, rows),
                )

    def test_a_split_that_loses_a_part_is_refused(self):
        """The post-condition is what states the exactness guarantee."""
        exact = [Decimal('6.0000'), Decimal('3.0000')]
        calculations.assert_parts_reconcile(exact, Decimal('9.0000'), 'test split')
        for lost in (Decimal('0.0001'), Decimal('-0.0001')):
            with self.assertRaisesRegex(RuntimeError, 'test split'):
                calculations.assert_parts_reconcile(
                    [exact[0], money(exact[1] - lost)], Decimal('9.0000'), 'test split',
                )

    def test_every_exact_split_checks_itself_before_returning(self):
        """Both splits route their result through the post-condition.

        Neither loop is exact because it is written carefully; it is exact
        because this check would fail if a restructuring dropped a residual.
        """
        with mock.patch.object(
                calculations, 'assert_parts_reconcile',
                wraps=calculations.assert_parts_reconcile,
        ) as guard:
            positions = distribute_money(Decimal('10.0000'), 3)
            shares = proportional_refund(Decimal('9.0000'), self.available(
                [Decimal('6.0000'), Decimal('3.0000')],
            ))
        self.assertEqual(guard.call_args_list, [
            mock.call(positions, Decimal('10.0000'), 'money distribution'),
            mock.call(
                [share['total_incl_tax'] for share in shares],
                Decimal('9.0000'), 'refund allocation',
            ),
        ])
