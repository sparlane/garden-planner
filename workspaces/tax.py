"""One rule for the tax rate a line takes when nobody entered one.

A workspace states the rate it charges once, on its settings screen. Every
document line then has a rate of its own, because the rate a supply was made at
is part of the supply and a return reports it years later -- but nobody should
have to type it again on a line that is charged at the ordinary rate.

So an omitted rate is filled from ``Workspace.default_tax_rate``. What makes
that safe is that it is a *fill* and not an override: a rate the writer stated
always wins, including a stated zero, because an import and an overseas invoice
are ordinary and the operator entering one is correcting the default on purpose.

Nothing here is effective-dated. Each document stores the rate it used, so a
workspace that changes its rate changes what the next line opens with and
nothing that is already recorded.

The exception is the whole of the rest of the rule. Only a standard-rated supply
carries a rate; a zero-rated export, an exempt supply, something outside the tax
altogether and a supply nobody has classified yet are all a rate of zero, and
the database says so -- ``sales_line_tax_treatment_matches_rate``
(``sales.SalesOrderLine``) is a check constraint, and ``StockReceiptLine`` and
``SupplierInvoiceLine`` refuse the same pairing in validation. Filling a rate
into one of those would not be a convenience; it would put a figure in the wrong
box of a return, or refuse the line outright.
"""

from decimal import Decimal

from .models import get_current_workspace

#: What every treatment other than the standard rate comes to.
ZERO = Decimal('0.0000')

#: The one treatment that carries a rate. Each model spells the unclassified
#: state its own way -- ``unclassified`` on a sales line, ``unknown`` on a
#: receipt or invoice line -- so the rule asks what a treatment *is* rather than
#: listing what it is not, and a vocabulary that gains a sixth treatment is
#: covered without being edited here.
STANDARD = 'standard'


def unentered_tax_rate(workspace, tax_treatment=''):
    """Return the rate a new line takes when the writer named none.

    A blank treatment means the line has not been classified away from the
    standard rate: sales lines leave it blank on the way in and derive it from
    the rate, and a serializer whose model has no treatment column at all passes
    nothing. Anything else named is a treatment that carries no rate.
    """
    if tax_treatment and tax_treatment != STANDARD:
        return ZERO
    return workspace.default_tax_rate


class TaxRateInputSerializerMixin:
    """Fill an omitted ``tax_rate`` from the workspace's default rate.

    Mix into the serializer an operator enters a line through, ahead of the
    serializer class itself. It fills on create only: an update names the line
    it is changing, and a rate already recorded is history rather than input.
    """

    #: The treatment a line is stored with when the request names none. Blank
    #: where the model derives the treatment from the rate, and the model's own
    #: default where it does not -- ``unknown`` is a positive statement that
    #: nobody has classified the supply, so it takes no rate.
    unstated_tax_treatment = ''

    def fill_tax_rate(self, attrs):
        """Return the validated data with the rate this workspace charges.

        Call it from a ``validate`` of your own; a serializer with nothing else
        to check inherits the one below instead.
        """
        if self.instance is not None or 'tax_rate' in attrs:
            return attrs
        treatment = attrs.get('tax_treatment', self.unstated_tax_treatment)
        attrs['tax_rate'] = unentered_tax_rate(get_current_workspace(), treatment)
        return attrs

    def validate(self, attrs):
        """Fill the rate, for a serializer with nothing else to validate."""
        return self.fill_tax_rate(super().validate(attrs))
