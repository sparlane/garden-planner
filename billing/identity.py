"""Who the parties to a supply were, as at the date a document was issued.

A document is evidence, so the names on it have to be the names that applied
when it was handed over. Reading the workspace or the customer live at print
time would quietly restate a two-year-old invoice the moment somebody fixes a
typo in an address, which is the opposite of what evidence is for. Everything
here therefore returns plain values to be *stored on the document*, never a
relationship to be followed later.

The GST number is the clearest case. It lives on task 117's dated registration
history rather than on the workspace, so the number in force on 3 May is
readable even after the registration changes in June — and the arrangement
itself is recorded alongside it, which is the link a filed return reconciles
through.
"""

from tax.periods import registration_in_force


def seller_snapshot(workspace, on_date):
    """Return the seller identity to store on a document issued on a date.

    `taxable_supply` comes back with it because the two answers are the same
    lookup: a workspace with no registration in force on the date is not making
    a taxable supply, so it carries no GST number and its document is an
    ordinary sales receipt rather than taxable supply information.
    """
    registration = registration_in_force(workspace, on_date)
    return {
        'seller_legal_name': workspace.legal_name.strip(),
        'seller_trading_name': workspace.trading_name.strip(),
        'seller_address': workspace.business_address.strip(),
        'seller_gst_number': registration.gst_number if registration else '',
        'seller_registration': registration,
        'taxable_supply': registration is not None,
    }


def buyer_snapshot(customer, overrides=None):
    """Return the buyer identity to store, defaulting from the customer record.

    A supply over a thousand dollars has to identify its recipient, and the
    identifier need not be a postal address — an NZBN, a phone number or an
    email address will do. So a customer with only an email on file can still
    be invoiced for a large supply, and the overrides let a one-off buyer be
    named on a document without creating a customer record nobody will reuse.
    """
    values = {'buyer_name': '', 'buyer_address': '', 'buyer_identifier': ''}
    if customer is not None:
        values['buyer_name'] = customer.name.strip()
        values['buyer_address'] = customer.billing_address.strip()
        values['buyer_identifier'] = customer.email.strip() or customer.phone.strip()
    for field, value in (overrides or {}).items():
        if field not in values:
            raise KeyError(f'Unknown buyer field: {field!r}')
        if value is not None:
            values[field] = value.strip()
    return values
