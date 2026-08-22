"""Pin Inland Revenue's check-digit routine for IRD/GST numbers.

Every number in this file was produced by the algorithm itself, so the table
proves internal consistency rather than agreement with Inland Revenue. The
weightings in ``tax/ird.py`` are the published ones; if they are ever revised,
this is the file that has to be regenerated with them.
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .ird import ird_check_digit, normalize_ird_number, validate_ird_number


class NormalizeIrdNumberTests(SimpleTestCase):
    """An operator copies a number off a letter, spaces and dashes included."""

    def test_eight_digit_form_gains_its_leading_zero(self):
        """The eight- and nine-digit forms are the same number, so both must pass."""
        self.assertEqual(normalize_ird_number('10000009'), '010000009')

    def test_nine_digit_form_is_unchanged(self):
        """A number already in its canonical form must survive normalization."""
        self.assertEqual(normalize_ird_number('010000009'), '010000009')

    def test_the_conventional_grouped_formats_are_accepted(self):
        """XX-XXX-XXX and XXX-XXX-XXX are how the number is actually printed.

        Both of these are Inland Revenue's own published worked examples, so
        they check the weightings against a source outside this file as well as
        checking that the grouping is stripped.
        """
        self.assertEqual(normalize_ird_number('49-091-850'), '049091850')
        self.assertEqual(normalize_ird_number('136-410-132'), '136410132')

    def test_separators_are_stripped(self):
        """Inland Revenue prints the number grouped, and people type it that way."""
        self.assertEqual(normalize_ird_number('010-000-009'), '010000009')
        self.assertEqual(normalize_ird_number(' 010 000 009 '), '010000009')
        self.assertEqual(normalize_ird_number('49 091 850'), '049091850')

    def test_empty_value_is_rejected(self):
        """A blank number must fail loudly rather than normalize to zeroes."""
        for value in ('', '   ', None):
            with self.assertRaises(ValidationError):
                normalize_ird_number(value)

    def test_non_digits_are_rejected(self):
        """Letters are a typo, not a separator, so they must not be stripped."""
        with self.assertRaises(ValidationError):
            normalize_ird_number('01000000A')

    def test_wrong_length_is_rejected(self):
        """Seven or ten digits cannot be padded into a valid number."""
        for value in ('1000000', '0100000090'):
            with self.assertRaises(ValidationError):
                normalize_ird_number(value)


class IrdCheckDigitTests(SimpleTestCase):
    """The secondary weighting exists precisely for the bases the primary fails."""

    def test_primary_weighting_produces_the_check_digit(self):
        """The ordinary case: the first weighting yields a writable digit."""
        self.assertEqual(ird_check_digit('01000000'), 9)
        self.assertEqual(ird_check_digit('12345678'), 5)

    def test_secondary_weighting_is_used_when_the_primary_yields_ten(self):
        """Ten is not a digit, so the routine retries rather than rejecting."""
        self.assertEqual(ird_check_digit('01000019'), 1)

    def test_a_base_both_weightings_fail_has_no_check_digit(self):
        """No ninth digit can match, so the base cannot appear in a valid number."""
        self.assertIsNone(ird_check_digit('01000005'))


class ValidateIrdNumberTests(SimpleTestCase):
    """A format check accepts every transposition, which is the actual mistake."""

    def test_valid_numbers_pass(self):
        """Both weightings, both lengths, and both grouped formats."""
        for value in ('010000009', '10000009', '010000191', '123456785',
                      '49-091-850', '136-410-132', '049091850', '136410132'):
            with self.subTest(value=value):
                validate_ird_number(value)

    def test_a_transposed_pair_is_rejected(self):
        """This is the reason the check digit exists rather than a length rule."""
        for value in ('213456785', '132456785', '124356785', '123456784'):
            with self.assertRaises(ValidationError):
                validate_ird_number(value)

    def test_a_base_with_no_check_digit_is_rejected(self):
        """Both weightings yielded ten, so every ninth digit must fail."""
        for last in '0123456789':
            with self.assertRaises(ValidationError):
                validate_ird_number(f'01000005{last}')

    def test_numbers_below_the_issued_range_are_rejected(self):
        """The check digit is correct here; only the range makes it impossible."""
        self.assertEqual(ird_check_digit('00099999'), 7)
        with self.assertRaises(ValidationError):
            validate_ird_number('000999997')

    def test_numbers_above_the_issued_range_are_rejected(self):
        """As above, at the other end: a good checksum on a number never issued."""
        self.assertEqual(ird_check_digit('15000000'), 9)
        with self.assertRaises(ValidationError):
            validate_ird_number('150000009')
