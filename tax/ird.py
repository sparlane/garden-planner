"""Validation for the IRD number an Inland Revenue registration is held under.

A GST number and an IRD number are the same nine digits, so one validator
serves both. The algorithm is Inland Revenue's published check-digit routine
rather than a length or format guess: a transposed pair of digits is the
mistake an operator actually makes when copying a number off a letter, and a
format check accepts every one of them.

Pure functions only. Nothing here touches the database or Django settings, so
the checksum table in ``tax/test_ird.py`` can pin the arithmetic directly.
"""

from django.core.exceptions import ValidationError


#: Applied to the first eight digits. When these produce a check digit of 10 —
#: which is not a digit — Inland Revenue's routine retries with the secondary
#: set rather than declaring the number invalid.
PRIMARY_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
SECONDARY_WEIGHTS = (7, 4, 3, 2, 5, 2, 7, 6)

#: Numbers below the floor were never issued; the ceiling is the top of the
#: range Inland Revenue allocates from. Both bounds are inclusive of the floor
#: and exclusive of the ceiling, matching how the range is published.
MINIMUM_IRD_NUMBER = 10_000_000
MAXIMUM_IRD_NUMBER = 150_000_000

#: Everything a human puts between the digits when copying a number down.
SEPARATORS = ' -‐‑‒–—'


def normalize_ird_number(value):
    """Return the nine-digit form of an entered IRD number.

    Accepts the eight-digit form, which is the same number without its leading
    zero, and strips the spaces and dashes people group the digits with.
    Raises when the result could not be a number at all; the check digit is
    ``validate_ird_number``'s business.
    """
    text = str(value or '')
    for separator in SEPARATORS:
        text = text.replace(separator, '')
    if not text:
        raise ValidationError('Enter an IRD/GST number.')
    if not text.isdigit():
        raise ValidationError('An IRD/GST number contains only digits.')
    if len(text) not in (8, 9):
        raise ValidationError('An IRD/GST number has eight or nine digits.')
    return text.zfill(9)


def ird_check_digit(base):
    """Return the check digit for eight base digits, or None if there is none.

    None means the number cannot be valid: both weightings produced 10, which
    is not writable as a single digit, so no ninth digit could ever match.
    """
    for weights in (PRIMARY_WEIGHTS, SECONDARY_WEIGHTS):
        total = sum(int(digit) * weight for digit, weight in zip(base, weights))
        remainder = total % 11
        check = 0 if remainder == 0 else 11 - remainder
        if check != 10:
            return check
    return None


def validate_ird_number(value):
    """Raise ValidationError unless the value is a well-formed IRD/GST number."""
    digits = normalize_ird_number(value)
    number = int(digits)
    if not MINIMUM_IRD_NUMBER <= number < MAXIMUM_IRD_NUMBER:
        raise ValidationError('That number is outside the range Inland Revenue issues.')
    check = ird_check_digit(digits[:8])
    if check is None or check != int(digits[8]):
        raise ValidationError('That IRD/GST number fails its check digit.')
