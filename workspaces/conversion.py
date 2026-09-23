"""How a foreign amount becomes an amount in the workspace's own currency.

A workspace converts into `Workspace.currency_code` and into nothing else. An
NZ workspace sets that to NZD and gets its NZD equivalents; a workspace left on
the default gets its equivalents in USD. Which code a New Zealand return ought
to be filed in is stated in exactly one place, `tax.turnover`'s
`workspace_currency_not_nzd` warning, and this module deliberately has no
opinion about it: hardcoding NZD here would make every amount in an existing
workspace foreign overnight.

The rate is typed per transaction. There is no rate table, so nothing here
looks a rate up; what it does is say how a rate that was typed applies. Three
decisions live here because every app that converts has to make them the same
way:

* **Which way the rate is quoted.** A rate is a ratio between two currencies
  and a number on its own does not say which way round. `QuoteDirection` makes
  the caller say, because a published rate is quoted one way on one page and
  the other way on the next, and the two answers differ by more than rounding.
* **How far a converted amount is rounded.** Each amount is converted on its
  own and rounded to the four places every money column in the repository uses.
  Converting independently is what keeps a converted line reconcilable to the
  line it came from; the cost is that converted lines may not add up to a
  converted total to the last cent, which is stated where it shows rather than
  forced away by pushing the remainder onto whichever line happens to be last.
* **Which method a workspace converts by.** A return that mixes a spot rate on
  one purchase with a period-end rate on the next is not converted
  consistently, so a workspace names one method and the others are refused.
  The identity conversion of an amount already in the workspace's currency is
  not a method an operator chooses and is always allowed.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import models


#: A rate carries ten decimal places, which is enough to hold a published rate
#: for any pair worth converting -- including one quoted the small way round,
#: where the significant digits start well to the right of the point -- without
#: the stored rate being a rounding of the rate that was actually used.
RATE_DIGITS = 18
RATE_PLACES = 10
RATE_QUANTUM = Decimal('0.0000000001')

#: Converted money is rounded to `bookkeeping.MONEY_PLACES`. The quantum is
#: restated rather than imported because `bookkeeping` imports this app and not
#: the other way round, and a currency rule that a workspace owns should not
#: need a tax-records app to be importable before it can be stated.
MONEY_QUANTUM = Decimal('0.0001')

ONE = Decimal('1')


class ConversionMethod(models.TextChoices):
    """How the rate applied to a transaction was arrived at."""

    SPOT = 'spot', 'Spot rate on the transaction date'
    PERIOD_END = 'period_end', 'Rate at the end of the period'
    PUBLISHED = 'published', 'Configured published rate'
    BASE_CURRENCY = 'base_currency', 'Already in the workspace currency'


#: The methods an operator chooses between. `BASE_CURRENCY` is not one of them:
#: it is what an amount already in the workspace's currency gets, at a rate of
#: one, and it is recorded so that every taxable transaction carries a
#: conversion rather than only the foreign ones.
POLICY_CHOICES = [
    choice for choice in ConversionMethod.choices
    if choice[0] != ConversionMethod.BASE_CURRENCY
]


class QuoteDirection(models.TextChoices):
    """Which of the two currencies the rate is quoted per unit of."""

    TARGET_PER_SOURCE = (
        'target_per_source',
        'Workspace currency per one unit of the transaction currency',
    )
    SOURCE_PER_TARGET = (
        'source_per_target',
        'Transaction currency per one unit of the workspace currency',
    )


def quantize_rate(value):
    """Round a typed rate to the places a rate is stored at."""
    return Decimal(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_money(value):
    """Round a converted amount to the places money is stored at."""
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def convert_amount(amount, rate, direction):
    """Convert one amount at one rate, rounding it on its own.

    Every amount on a transaction goes through here separately, at the same
    rate, so that each converted figure is the conversion of the figure beside
    it rather than a share of a converted total.
    """
    rate = Decimal(rate)
    if rate <= 0:
        raise ValueError('A conversion rate must be above zero.')
    amount = Decimal(amount)
    if direction == QuoteDirection.SOURCE_PER_TARGET:
        return quantize_money(amount / rate)
    return quantize_money(amount * rate)


def conversion_policy_refusal(workspace, method):
    """Say why this workspace does not convert an amount by this method.

    None where there is nothing to say: the identity conversion, which no
    policy governs, and the one method the workspace has chosen. Refusing the
    rest is what "a consistent workspace conversion policy" means -- a return
    that took a spot rate on one purchase and a period-end rate on the next is
    not converted consistently, and the difference between them is real money
    rather than a presentation choice.
    """
    if method in (ConversionMethod.BASE_CURRENCY, workspace.conversion_policy):
        return None
    chosen = ConversionMethod(workspace.conversion_policy).label
    return (
        f'This workspace converts every foreign amount at the '
        f'{_sentence_label(chosen)}, so a {_sentence_label(_label_of(method))} '
        f'cannot be recorded. Change the conversion policy in workspace '
        f'settings to convert another way.'
    )


def consistent_policy_refusal(methods):
    """Say why these conversions do not belong in one return.

    The policy check above is made where a rate is typed, so it cannot see a
    rate typed before the policy was changed. A year or a period is therefore
    asked separately whether everything in it was converted the same way, which
    is the question a return actually has. The identity conversion is not a
    method, so it never makes a set inconsistent.
    """
    chosen = sorted({
        method for method in methods
        if method and method != ConversionMethod.BASE_CURRENCY
    })
    if len(chosen) < 2:
        return None
    return (
        f'Amounts in this return were converted by more than one method: '
        f'{", ".join(chosen)}. One return is converted one way.'
    )


def _label_of(method):
    """Return the human label for a method, or the raw value if unknown."""
    try:
        return ConversionMethod(method).label
    except ValueError:
        return str(method)


def _sentence_label(label):
    """Lower-case a choice label for use mid-sentence."""
    return label[:1].lower() + label[1:]
