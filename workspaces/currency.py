"""One rule for the currency an operator may enter on a record.

A workspace with multiple currencies turned off is saying it trades in one, so
its screens ask for no currency at all and file every new record in
``Workspace.currency_code``. The rule is still stated on the server, because
the browser is not the only client and because the subject is money: a
currency that arrives anyway is refused by name rather than overwritten, which
would file an amount under a code the caller did not send, or ignored, which
would report success for a request that was not carried out.

It governs input and nothing else. Nothing here reads a stored currency, so a
record already in another one keeps it, is still displayed in it, and is still
refused a combined total by ``costing.currency``.
"""

from rest_framework import serializers

from .models import get_current_workspace


def currency_input_refusal(workspace, value):
    """Say why this workspace cannot record a new amount in this currency.

    None where there is nothing to say: any currency while the switch is on,
    and the workspace's own while it is off. Case is not part of the
    comparison, because a code an operator typed in lower case is the same
    code -- but it is not the same *string*, which is why the mixin below
    stores what it accepted rather than what it was handed.
    """
    if workspace.multi_currency_enabled or value is None:
        return None
    entered = value.strip().upper()
    if entered == workspace.currency_code.upper():
        return None
    return (
        f'This workspace records every amount in {workspace.currency_code}, so '
        f'{entered} cannot be entered. Turn on multiple currencies in workspace '
        f'settings to record another.'
    )


class CurrencyInputSerializerMixin:  # pylint: disable=too-few-public-methods
    """Refuse a ``currency_code`` the workspace does not record amounts in.

    Mix into any serializer carrying the field an operator enters a currency
    through, ahead of the serializer class itself. It does nothing to a
    serializer without the field, and nothing to a request that does not send
    one -- an omitted currency is filled in from the workspace where the
    serializer already did so, which is what "the workspace's own currency is
    used" means.

    What it accepts, it stores in upper case. Only four of the columns it
    reaches state the shape of a code themselves -- ``StockReceipt``,
    ``PurchaseOrder``, ``SupplierInvoice`` and ``SalesOrder`` carry the ISO
    4217 validator, and it does run before this method. The rest are a bare
    three-character column, so ``nzd`` would be stored exactly as typed and
    then fail to equal ``NZD`` in every reader that asks: ``build_report``
    (``bookkeeping.services``), the grouping task 142 refuses a mixture on,
    ``post_receipt`` (``inventory.ledger``) and ``sales.fill_containers`` all
    compare the three letters and nothing else.
    """

    def validate_currency_code(self, value):
        """Accept the workspace's own currency, and another only while allowed.

        An update naming the currency the record already carries is that
        record saying again what it says, not an amount newly entered abroad,
        so it is accepted whatever the switch says. It has to be: a record
        already in another currency keeps it, and ``PurchaseOrderViewSet`` and
        ``SupplierInvoiceViewSet`` offer PUT and no PATCH over two write
        serializers that require the field, so every edit to a euro draft
        names a currency. Refusing the echo left such a draft editable only by
        refiling recorded money in the workspace's own currency, which is the
        one thing this task promised not to do.

        It is the *record's* currency, not a second permitted answer: a create
        has no instance to echo, and one euro draft does not let the next new
        document be entered in euros.
        """
        entered = value if value is None else value.strip().upper()
        recorded = getattr(self.instance, 'currency_code', None)
        if entered is not None and recorded and entered == recorded.upper():
            return entered
        refusal = currency_input_refusal(get_current_workspace(), value)
        if refusal:
            raise serializers.ValidationError(refusal)
        return entered
