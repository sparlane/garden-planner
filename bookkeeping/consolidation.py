"""Add up amounts that were recorded in more than one currency.

A GST return and an income-tax schedule both have the same problem and have to
answer it the same way: the figures behind them were recorded in whatever
currency each transaction happened in, and a return states one. This is where
they meet -- one object that knows every rate typed in a workspace, converts an
amount at the rate belonging to the row it came from, and remembers what it
could not convert.

Three rules it applies, all of them task 121's decisions rather than this
module's:

* **An amount already in the workspace's currency needs no record.** It is
  converted at one because there is nothing to convert. The identity
  conversions the backfill records exist so that an export can show a rate
  against every transaction; a reader does not need to find one to know that
  dollars are dollars.
* **A foreign amount with no rate typed is not converted, and not guessed.**
  It is counted, named, and reported as a data-quality finding. The total it
  belonged to is withheld rather than stated short, because a total missing one
  of its rows is not a smaller true total.
* **An unconverted row does not block anything.** It blocks a *figure*: a year
  can be finalized and filed with one, showing as a finding. That is
  deliberately not how `provisional` stock behaves, which does block, because
  an unknown cost and an untyped rate are different kinds of missing.
"""

from decimal import Decimal

from workspaces.conversion import (
    consistent_policy_refusal,
    convert_amount,
    quantize_money,
)

from .conversion import live_conversions


ZERO = Decimal('0')

#: What a report says about a row whose rate has not been typed. One sentence
#: in one place, because the GST report and the income-tax report are answering
#: the same question and an operator reading both should not have to work out
#: whether they mean the same thing.
UNCONVERTED_MESSAGE = (
    'Some amounts were recorded in another currency and no exchange rate has '
    'been recorded against them. They are reported in the currency they were '
    'recorded in, and any figure that would have added one of them up states '
    'nothing rather than a total missing a row.'
)

#: What it says where converted figures are published. The four-place rounding
#: is applied to each amount separately, so the parts of a converted total can
#: differ from the converted total by a cent; saying so is task 121's decision
#: about what to do with that remainder.
ROUNDING_NOTE = (
    'Every amount is converted on its own and rounded to four places, so '
    'converted lines can differ from a converted total by a cent.'
)


def rate_source(row):
    """Say which record's rate converts this row.

    Usually the row's own. Three report rows are derived from something one
    level down -- an expense line off a supplier invoice, a depreciation
    schedule off the asset it depreciates -- and the rate belongs to the
    transaction, not to the derivation, so those rows name it explicitly.
    """
    return (
        row.get('rate_source_type', row['source_type']),
        row.get('rate_source_id', row['source_id']),
        row['currency_code'],
    )


class Converter:
    """Convert a workspace's amounts into its own currency, once per report."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.target = workspace.currency_code
        self._rates = live_conversions(workspace)
        #: Every row a rate was wanted for and not found, as
        #: (source type, source id, the currency it was recorded in).
        self.unconverted = set()
        #: Every method actually used, for the consistency check a return needs.
        self.methods = set()

    def amount(self, source_type, source_id, currency_code, amount):
        """Return one amount in the workspace's currency, or None.

        None means one thing: the amount is foreign and nobody has typed a rate
        against the record it came from. Every caller treats that as a figure
        it cannot state.
        """
        if amount is None:
            return None
        if currency_code == self.target:
            return quantize_money(amount)
        record = self._rates.get((source_type, str(source_id)))
        if record is None or not self._converts(record, currency_code):
            self.unconverted.add((source_type, str(source_id), currency_code))
            return None
        self.methods.add(record.method)
        return convert_amount(amount, record.rate, record.quote_direction)

    def _converts(self, record, currency_code):
        """Whether this record is a rate between the two currencies in hand.

        A conversion names the pair it was recorded for, and a pair can go out
        of date underneath it: a workspace that recorded euros and later files
        in dollars has a table full of identity conversions saying EUR to EUR
        at one, against rows that are now foreign. Reading one of those as the
        rate would restate 23.00 EUR as 23.00 NZD and call the return complete,
        which is the one thing this module exists to stop. A record for the
        wrong pair is treated as no record at all, so the figure is withheld
        and the row is reported as awaiting a rate -- which it is.
        """
        pair = (record.source_currency_code, record.target_currency_code)
        return pair == (currency_code, self.target)

    def total(self, items):
        """Add a series of amounts, or return None if any could not be converted.

        `items` yields (source type, source id, currency code, amount). A total
        of nothing is zero: a period that saw no trading is nil, not unknown.
        """
        total = ZERO
        stateable = True
        for source_type, source_id, currency_code, amount in items:
            converted = self.amount(source_type, source_id, currency_code, amount)
            if converted is None:
                stateable = False
                continue
            total += converted
        return total if stateable else None

    def row_total(self, rows, field='amount'):
        """Add report rows, which already carry their own source and currency."""
        return self.total(rate_source(row) + (Decimal(row[field]),) for row in rows)

    def converted_row(self, row, field='amount'):
        """Return one report row with what its amount comes to beside it."""
        converted = self.amount(*rate_source(row), Decimal(row[field]))
        return {
            **row,
            'converted_amount': None if converted is None else f'{converted:.4f}',
            'converted_currency_code': self.target,
        }

    @property
    def complete(self):
        """Whether every amount asked about so far could be converted."""
        return not self.unconverted

    def findings(self):
        """Return the data-quality entries the conversions themselves earned.

        Two, at most. One says a rate is missing; the other says a return holds
        amounts converted by more than one method, which the policy check
        cannot catch on its own because a policy changed mid-year leaves both
        methods standing behind it.
        """
        entries = []
        if self.unconverted:
            entries.append({
                'code': 'unconverted_source',
                'count': len(self.unconverted),
                # Not blocking. A year can be finalized and filed holding one;
                # what it cannot do is state a figure that would have added it
                # up. That is task 121's decision, recorded here because this
                # is where every reader of the finding starts.
                'blocking': False,
                'message': UNCONVERTED_MESSAGE,
                'currencies': sorted({row[2] for row in self.unconverted}),
            })
        refusal = consistent_policy_refusal(self.methods)
        if refusal:
            entries.append({
                'code': 'inconsistent_conversion_method',
                'count': len(self.methods),
                # Blocking, because it is fixable and because a return
                # converted two ways is not converted consistently: retype the
                # rates that do not match the workspace's policy.
                'blocking': True,
                'message': refusal,
            })
        return entries
