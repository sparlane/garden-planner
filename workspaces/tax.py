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
altogether and a supply nobody has classified yet are all a rate of zero.
``sales_line_tax_treatment_matches_rate`` (``sales.SalesOrderLine``) is a check
constraint saying so, and ``StockReceiptLine._tax_errors`` refuses the same
pairing in validation. ``SupplierInvoiceLine`` does **not** check it, which is
why the rule is stated here rather than left to each model: a rate filled onto a
line the supplier's bill never charged reaches the GST entry (``tax.entries``)
and the GST detail export (``reporting.gst``) as a standard-rated line with nil
tax, and nothing in between would have refused it.
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
    serializer class itself. It fills on create only: an update names the
    document it is changing, and a rate already recorded is history rather than
    input.
    """

    #: The treatment a line is stored with when the request names none. Blank
    #: where the model derives the treatment from the rate, and the model's own
    #: default where it does not -- ``unknown`` is a positive statement that
    #: nobody has classified the supply, so it takes no rate.
    unstated_tax_treatment = ''

    @property
    def tax_rate_workspace(self):
        """The workspace whose rate fills this line, read once per request.

        ``get_current_workspace`` is a query, and a nested write validates every
        line through one reused child serializer, so reading it here is one
        query per request rather than one per line.
        """
        workspace = getattr(self, '_tax_rate_workspace', None)
        if workspace is None:
            workspace = get_current_workspace()
            self._tax_rate_workspace = workspace
        return workspace

    def tax_rate_is_fillable(self, attrs):  # pylint: disable=unused-argument
        """Whether the workspace's rate belongs on this line at all.

        True unless a serializer says otherwise. Overridden where the document
        states its tax as an amount rather than deriving it from a rate, so a
        bill charging nil tax is not given a rate beside the nil.
        """
        return True

    def fill_tax_rate(self, attrs):
        """Return the validated data with the rate this workspace charges.

        Call it from a ``validate`` of your own; a serializer with nothing else
        to check inherits the one below instead.

        The guard asks ``self.root.instance``, not ``self.instance``: a nested
        line is validated by a child serializer that is constructed once with no
        instance and reused for every line, so a child asking itself would
        report a create on every update and re-rate stored lines each time a
        document was re-sent.
        """
        if self.root.instance is not None or 'tax_rate' in attrs:
            return attrs
        if not self.tax_rate_is_fillable(attrs):
            return attrs
        treatment = attrs.get('tax_treatment', self.unstated_tax_treatment)
        attrs['tax_rate'] = unentered_tax_rate(self.tax_rate_workspace, treatment)
        return attrs

    def validate(self, attrs):
        """Fill the rate, for a serializer with nothing else to validate."""
        return self.fill_tax_rate(super().validate(attrs))
