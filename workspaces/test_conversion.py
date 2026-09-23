"""The rate arithmetic and the conversion policy every app shares (task 121).

Nothing here touches a stored record. These are the three decisions
`workspaces.conversion` exists to make once -- which way a rate is quoted, how
far a converted amount is rounded, and which method a workspace converts by --
pinned at the figures the rest of task 121's tests reuse.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring

from decimal import Decimal

from tests.api import RESTContractTestCase

from .conversion import (
    ConversionMethod,
    QuoteDirection,
    consistent_policy_refusal,
    convert_amount,
    conversion_policy_refusal,
    quantize_rate,
)
from .models import Workspace, get_current_workspace


class RateArithmeticTests(RESTContractTestCase):
    """Verification 1: one rate, two quote directions, four places."""

    def test_a_rate_quoted_per_unit_of_the_transaction_currency_multiplies(self):
        self.assertEqual(
            convert_amount(
                Decimal('100.0000'),
                Decimal('1.8234567890'),
                QuoteDirection.TARGET_PER_SOURCE,
            ),
            Decimal('182.3457'),
        )

    def test_a_rate_quoted_per_unit_of_the_workspace_currency_divides(self):
        self.assertEqual(
            convert_amount(
                Decimal('100.0000'),
                Decimal('0.5485000000'),
                QuoteDirection.SOURCE_PER_TARGET,
            ),
            Decimal('182.3154'),
        )

    def test_the_two_directions_are_not_the_same_number(self):
        """Which way a published rate is quoted is real money, not a label."""
        forward = convert_amount(
            Decimal('100.0000'), Decimal('1.8234567890'),
            QuoteDirection.TARGET_PER_SOURCE,
        )
        backward = convert_amount(
            Decimal('100.0000'), Decimal('1.8234567890'),
            QuoteDirection.SOURCE_PER_TARGET,
        )
        self.assertEqual(f'{backward:.4f}', '54.8409')
        self.assertNotEqual(forward, backward)

    def test_a_converted_amount_rounds_half_up(self):
        self.assertEqual(
            convert_amount(
                Decimal('1.0000'),
                Decimal('1.0000500000'),
                QuoteDirection.TARGET_PER_SOURCE,
            ),
            Decimal('1.0001'),
        )

    def test_a_rate_keeps_ten_places_and_rounds_half_up_at_the_eleventh(self):
        self.assertEqual(
            quantize_rate(Decimal('1.23456789005')), Decimal('1.2345678901'),
        )
        self.assertEqual(f'{quantize_rate(Decimal("1.5")):f}', '1.5000000000')

    def test_converted_lines_need_not_add_up_to_a_converted_total(self):
        """Each amount is converted on its own, and the remainder is stated.

        Two ten-unit lines and their twenty-unit total at the same rate: the
        lines come to 6.6666 and the total to 6.6667. Nothing pushes the cent
        onto either line, because the line it landed on would then not be the
        conversion of the line beside it.
        """
        rate = Decimal('0.3333333333')
        lines = [
            convert_amount(Decimal('10.0000'), rate, QuoteDirection.TARGET_PER_SOURCE)
            for _ in range(2)
        ]
        total = convert_amount(
            Decimal('20.0000'), rate, QuoteDirection.TARGET_PER_SOURCE,
        )
        self.assertEqual(f'{sum(lines):.4f}', '6.6666')
        self.assertEqual(f'{total:.4f}', '6.6667')

    def test_a_rate_of_one_leaves_an_amount_exactly_where_it_was(self):
        self.assertEqual(
            convert_amount(
                Decimal('123.4567'), Decimal('1'), QuoteDirection.TARGET_PER_SOURCE,
            ),
            Decimal('123.4567'),
        )

    def test_a_rate_that_is_not_above_zero_is_not_a_rate(self):
        for rate in (Decimal('0'), Decimal('-1.5')):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    convert_amount(
                        Decimal('1.0000'), rate, QuoteDirection.TARGET_PER_SOURCE,
                    )


class ConversionPolicyTests(RESTContractTestCase):
    """Verification 1: one workspace converts one way, consistently."""

    url = '/settings/workspace/'

    def test_a_workspace_converts_at_the_spot_rate_until_told_otherwise(self):
        self.assertEqual(
            get_current_workspace().conversion_policy, ConversionMethod.SPOT,
        )
        self.assertEqual(
            Workspace(name='Second bench').conversion_policy, ConversionMethod.SPOT,
        )

    def test_the_profile_publishes_the_policy_and_takes_it_back(self):
        self.assertEqual(self.client.get(self.url).data['conversion_policy'], 'spot')

        response = self.client.patch(
            self.url, {'conversion_policy': 'period_end'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get_current_workspace().conversion_policy, 'period_end')

    def test_the_policy_refuses_a_method_the_workspace_does_not_convert_by(self):
        workspace = get_current_workspace()
        self.assertIsNone(
            conversion_policy_refusal(workspace, ConversionMethod.SPOT),
        )
        self.assertEqual(
            conversion_policy_refusal(workspace, ConversionMethod.PERIOD_END),
            'This workspace converts every foreign amount at the spot rate on '
            'the transaction date, so a rate at the end of the period cannot '
            'be recorded. Change the conversion policy in workspace settings '
            'to convert another way.',
        )

    def test_every_policy_is_offered_and_each_refuses_the_others(self):
        workspace = get_current_workspace()
        for policy in ('spot', 'period_end', 'published'):
            with self.subTest(policy=policy):
                workspace.conversion_policy = policy
                allowed = [
                    method for method, _ in ConversionMethod.choices
                    if conversion_policy_refusal(workspace, method) is None
                ]
                self.assertEqual(
                    allowed, [policy, ConversionMethod.BASE_CURRENCY],
                )

    def test_an_amount_already_in_the_workspace_currency_needs_no_policy(self):
        """The identity conversion is not a method anybody chooses."""
        workspace = get_current_workspace()
        workspace.conversion_policy = ConversionMethod.PUBLISHED
        self.assertIsNone(
            conversion_policy_refusal(workspace, ConversionMethod.BASE_CURRENCY),
        )

    def test_a_return_may_not_mix_two_methods_even_where_both_were_allowed(self):
        """A policy changed mid-year leaves both methods standing in one year.

        The policy check is made where a conversion is recorded, so it cannot
        see a rate typed before the policy changed. Saying whether a set of
        conversions is consistent is a separate question, asked by the reports.
        """
        self.assertIsNone(consistent_policy_refusal(['spot', 'spot']))
        self.assertIsNone(consistent_policy_refusal([]))
        self.assertIsNone(
            consistent_policy_refusal(['spot', ConversionMethod.BASE_CURRENCY]),
        )
        self.assertEqual(
            consistent_policy_refusal(['spot', 'period_end']),
            'Amounts in this return were converted by more than one method: '
            'period_end, spot. One return is converted one way.',
        )
