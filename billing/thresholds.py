"""What a taxable supply document must state, and how its value decides that.

New Zealand asks for different information depending on how much a supply is
worth. A sale of twenty dollars needs the seller, the date, what was sold and
the amount; a sale of two thousand needs the buyer identified as well. Getting
that wrong in either direction has a cost — a defective document on one side,
and a walk-in customer asked for a postal address to buy a punnet on the other.

This module holds the whole of that judgement and touches no database at all,
in the same shape as `tax.recognition`. The consequence is that the matrix the
task asks to be verified — three value bands against every required element —
is a fast unit test rather than three fixtures.

Two elements — the quantity supplied, and that GST is stated — are satisfied by
construction for every document `billing` issues: a document line always covers
a whole number of commercial positions, and always carries its tax amount. That
is worth stating as a rule rather than assuming, because a printed document has
to show a reader that each requirement is met, and because a later document
shape that stopped guaranteeing them would fail here rather than silently.

Two things are deliberately not decided here:

* **Whether the seller is registered.** An unregistered nursery still issues
  documents; they are simply not taxable supply information, they carry no GST
  number, and they may not state GST. Passing `taxable_supply=False` drops the
  two GST elements rather than failing them, because an absent number is
  correct there and inventing one would be a false record.
* **What to do about a missing element.** This module names what is missing.
  `documents` refuses to issue, which is the point of change 5, but a screen
  that wants to show a checklist before anybody commits can ask the same
  question without writing anything.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple


#: Value bands, by the GST-inclusive total of the whole document.
LOW = 'low'
STANDARD = 'standard'
FULL = 'full'

TIERS = (LOW, STANDARD, FULL)

#: The inclusive upper bound of each band. A supply *of* $200 is in the low
#: band and a supply *over* $200 is not, which is why these are compared with
#: `<=` rather than `<`; the same holds at a thousand.
LOW_VALUE_LIMIT = Decimal('200')
STANDARD_LIMIT = Decimal('1000')

TIER_LABELS = {
    LOW: '$200 or less',
    STANDARD: 'Over $200 and up to $1,000',
    FULL: 'Over $1,000',
}

#: Element codes. Each names one piece of information a document has to carry,
#: and each is reported back by its own code so a caller can say precisely what
#: is absent instead of "the document is incomplete".
SELLER_NAME = 'seller_name'
SELLER_GST_NUMBER = 'seller_gst_number'
DOCUMENT_DATE = 'document_date'
SUPPLY_DESCRIPTION = 'supply_description'
TOTAL_PAYABLE = 'total_payable'
GST_STATED = 'gst_stated'
QUANTITY = 'quantity'
BUYER_NAME = 'buyer_name'
BUYER_IDENTIFICATION = 'buyer_identification'

ELEMENT_LABELS = {
    SELLER_NAME: 'the name of the supplier',
    SELLER_GST_NUMBER: 'the supplier GST number',
    DOCUMENT_DATE: 'the date of the supply',
    SUPPLY_DESCRIPTION: 'a description of what was supplied',
    TOTAL_PAYABLE: 'the total amount payable',
    GST_STATED: 'the GST amount, or that the total includes GST',
    QUANTITY: 'the quantity or volume supplied',
    BUYER_NAME: 'the name of the recipient',
    BUYER_IDENTIFICATION: 'an address or other identifier for the recipient',
}

#: Elements every document carries whatever it is worth.
_BASE = (
    SELLER_NAME,
    SELLER_GST_NUMBER,
    DOCUMENT_DATE,
    SUPPLY_DESCRIPTION,
    TOTAL_PAYABLE,
    GST_STATED,
)

#: What each band requires. Written as one table rather than as branching code
#: because the table is the rule: correcting a threshold means editing a line
#: here, and a test reads the same table back.
REQUIREMENTS = {
    LOW: _BASE,
    STANDARD: _BASE + (QUANTITY,),
    FULL: _BASE + (QUANTITY, BUYER_NAME, BUYER_IDENTIFICATION),
}

#: Elements that only make sense when the seller is GST registered. A document
#: from an unregistered seller states no GST and carries no number, so these
#: drop out of its requirements entirely rather than being failed.
_REGISTERED_ONLY = (SELLER_GST_NUMBER, GST_STATED)


def tier_for(total_incl_tax):
    """Return the value band one document's GST-inclusive total falls in."""
    total = Decimal(total_incl_tax)
    if total <= LOW_VALUE_LIMIT:
        return LOW
    return STANDARD if total <= STANDARD_LIMIT else FULL


def required_elements(tier, *, taxable_supply=True):
    """Return the element codes a document in one band has to carry."""
    if tier not in REQUIREMENTS:
        raise ValueError(f'Unknown supply tier: {tier!r}')
    elements = REQUIREMENTS[tier]
    if taxable_supply:
        return elements
    return tuple(code for code in elements if code not in _REGISTERED_ONLY)


@dataclass(frozen=True)
class DocumentInformation:  # pylint: disable=too-many-instance-attributes
    """What one document actually states, as plain values.

    Deliberately not a model instance. The rules run before anything is
    written, so a document that would be defective is refused rather than
    stored and flagged.
    """

    total_incl_tax: Decimal
    taxable_supply: bool
    seller_name: str
    seller_gst_number: str
    document_date: Optional[date]
    gst_stated: bool
    #: One description per line. A blank one fails the description element,
    #: because a document is only as described as its least described line.
    line_descriptions: Tuple[str, ...] = ()
    #: One quantity per line that supplies goods. A `None` in this tuple is a
    #: line supplying something nobody counted; a line that is not about goods
    #: at all is simply absent from it, because a payment on account has no
    #: volume and its description is what says so.
    supply_quantities: Tuple[Optional[Decimal], ...] = ()
    buyer_name: str = ''
    buyer_identification: str = ''

    @property
    def tier(self):
        """The value band this document falls in."""
        return tier_for(self.total_incl_tax)


def _present(information):
    """Return the elements this document does carry, as a set of codes."""
    carried = set()
    if information.seller_name.strip():
        carried.add(SELLER_NAME)
    if information.seller_gst_number.strip():
        carried.add(SELLER_GST_NUMBER)
    if information.document_date is not None:
        carried.add(DOCUMENT_DATE)
    descriptions = information.line_descriptions
    if descriptions and all(text.strip() for text in descriptions):
        carried.add(SUPPLY_DESCRIPTION)
    # Zero is a total a document may legitimately state — a fully discounted
    # supply — so presence is about the value existing, not about it being
    # positive. A negative total is not a supply and never reaches here.
    if information.total_incl_tax is not None:
        carried.add(TOTAL_PAYABLE)
    if information.gst_stated:
        carried.add(GST_STATED)
    quantities = information.supply_quantities
    if all(quantity is not None for quantity in quantities):
        carried.add(QUANTITY)
    if information.buyer_name.strip():
        carried.add(BUYER_NAME)
    if information.buyer_identification.strip():
        carried.add(BUYER_IDENTIFICATION)
    return carried


def missing_information(information):
    """Return the required element codes this document does not carry.

    An empty tuple means the document is complete for its own value band. The
    codes come back in the band's own order so a message reads the way the
    requirement is written rather than in whatever order a set iterated.
    """
    required = required_elements(
        information.tier, taxable_supply=information.taxable_supply,
    )
    carried = _present(information)
    return tuple(code for code in required if code not in carried)


def describe(codes):
    """Render element codes as one readable sentence fragment."""
    return ', '.join(ELEMENT_LABELS[code] for code in codes)
