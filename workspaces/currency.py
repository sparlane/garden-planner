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
    code; what shape a code may take is the model field's own validator to
    state, and it has already run by the time this is asked.
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
    """

    def validate_currency_code(self, value):
        """Accept the workspace's own currency, and another only while allowed."""
        refusal = currency_input_refusal(get_current_workspace(), value)
        if refusal:
            raise serializers.ValidationError(refusal)
        return value
