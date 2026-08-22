"""Issued taxable supply documents and the corrections that amend them.

A `Fulfillment` says plants left the nursery and a `Payment` says money came
in. Neither is a document anybody can hand a customer, and neither states who
made the supply, under what GST number, on what date. That is what this app
adds: a durable customer-facing record that points at the commerce facts
rather than restating them.

Three rules shape every model here.

**Nothing is recalculated.** A document snapshots the seller and buyer identity
in force when it was issued, and its money comes from the order line positions
it covers. Renaming the business afterwards does not rewrite a document already
given to somebody.

**Nothing is edited.** Both document kinds extend
`sales.ImmutableCommerceModel`, which refuses every update and delete for the
same reason a fulfillment does. Correcting an issued document means issuing a
correction against it, which is what New Zealand calls supply correction
information, and the original stays readable exactly as it was handed over.

**Nothing is invoiced twice.** A document line covers whole commercial
positions of one order line — the same one-based positions `FulfillmentLine`
already uses — and a position may be covered by only one live document. That is
what lets partial invoices, backorders and invoice-before-dispatch coexist
without any of them duplicating a revenue fact.

A deposit is not a line. Money taken in advance is a `Payment` that already
exists, and adding a deposit line here would put the same value on a document
twice: once as the deposit and once as the goods it was against. What a
document states instead is the three facts a customer needs to read a balance —
what was invoiced before this document, what has been paid up to its date, and
what is therefore still due. Each is snapshotted, because "paid to date" on a
document handed over in May must not change when June's cheque arrives.

There is deliberately no database constraint making a position unique across
documents. A wrong-rate correction credits a document in full and re-issues it,
and a unique index on the position would forbid exactly that legitimate act.
The rule is enforced in `documents` under a lock on the order, where "live"
can mean what it actually means: covered by a document that has not been
credited away.
"""

# The workspace, actor and timestamp declarations are the same lines every app
# that records who did something carries; `sales`, `tax` and `costing` repeat
# them for the identical reason.
# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from sales.models import ImmutableCommerceModel
from workspaces.models import Workspace

from .thresholds import TIERS


#: Number series. Each is counted separately so the invoices, the credit notes
#: and the debit notes are each contiguous — a gap in one series is a question
#: worth answering, and interleaving three kinds through one counter would put
#: gaps in all three by design.
SUPPLY_SERIES = 'supply'
CREDIT_SERIES = 'credit'
DEBIT_SERIES = 'debit'

SERIES_PREFIXES = {
    SUPPLY_SERIES: 'INV',
    CREDIT_SERIES: 'CRN',
    DEBIT_SERIES: 'DBN',
}


class DocumentNumberSequence(models.Model):
    """The next readable number in one series for one locked workspace.

    Keyed by series rather than being one row per workspace, which is the only
    difference from `sales.FulfillmentNumberSequence`; everything else about
    the locked-row pattern is the same.
    """

    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, related_name='+',
    )
    series = models.CharField(max_length=16)
    next_number = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'series'],
                name='billing_number_sequence_unique',
            ),
        ]

    def __str__(self):
        return f'{self.series} next {self.next_number}'


class PartySnapshotModel(models.Model):
    """Who supplied and who received, frozen as at the document date.

    Declared once and inherited by both document kinds. A correction has to
    identify the same two parties as the supply it corrects, and copying the
    block would be nine chances for the two to drift apart.

    The seller GST number is snapshotted from the registration in force on the
    document's own date rather than read live, so a document issued before a
    change of number keeps saying what it said. `seller_registration` records
    which arrangement that was, which is the link a filed return is reconciled
    through.
    """

    seller_legal_name = models.CharField(max_length=255)
    seller_trading_name = models.CharField(max_length=255, blank=True, default='')
    seller_address = models.TextField(blank=True, default='')
    seller_gst_number = models.CharField(max_length=16, blank=True, default='')
    seller_registration = models.ForeignKey(
        'tax.GstRegistration',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
        help_text=(
            'The GST arrangement in force on the document date, or empty when '
            'the seller was not registered and the document is therefore not '
            'taxable supply information.'
        ),
    )
    customer = models.ForeignKey(
        'sales.Customer',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
    )
    buyer_name = models.CharField(max_length=255, blank=True, default='')
    buyer_address = models.TextField(blank=True, default='')
    buyer_identifier = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=(
            'Any other identifier for the recipient a supply over $1,000 may '
            'be identified by instead of an address — an NZBN, a phone number, '
            'an email address, or a website.'
        ),
    )

    class Meta:
        abstract = True

    @property
    def buyer_identification(self):
        """Whichever identifier the recipient was recorded by, address first."""
        return self.buyer_address.strip() or self.buyer_identifier.strip()


class SupplyDocument(ImmutableCommerceModel, PartySnapshotModel):
    """One issued invoice, or receipt, for part or all of one order.

    `taxable_supply` is what separates the two. A GST-registered seller issues
    taxable supply information carrying a GST number; an unregistered one
    issues an ordinary sales receipt, which is a real document that simply may
    not mention GST. Both are kept here, because a nursery that registers
    halfway through a year needs one continuous record of what it sold.
    """

    document_number = models.CharField(max_length=32)
    order = models.ForeignKey(
        'sales.SalesOrder', on_delete=models.PROTECT, related_name='supply_documents',
    )
    issued_on = models.DateField(
        help_text=(
            'The document date, as a workspace-local business date. This is '
            'the time of supply the invoice basis recognises on, so it is a '
            'date rather than an instant.'
        ),
    )
    taxable_supply = models.BooleanField(
        help_text=(
            'Whether the seller was GST registered on the document date, and '
            'the document is therefore taxable supply information.'
        ),
    )
    tier = models.CharField(
        max_length=16,
        help_text='The value band whose required information this document met.',
    )
    currency_code = models.CharField(max_length=3)
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    previously_invoiced = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        help_text='The value of every live document issued against this order before this one.',
    )
    paid_to_date = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        help_text=(
            'Payments received net of refunds, on or before the document date. '
            'A deposit taken in advance shows up here rather than as a line.'
        ),
    )
    balance_due = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        help_text=(
            'What was still owed when this document was issued, floored at '
            'zero. An excess is reported separately rather than as a negative '
            'balance, because money beyond the value of a supply is not a '
            'discount on it.'
        ),
    )
    overpaid_at_issue = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        help_text='Payments beyond everything invoiced, as at the document date.',
    )
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['issued_on', 'pk']
        indexes = [
            models.Index(
                fields=['workspace', 'issued_on'],
                name='billing_document_date_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'document_number'],
                name='billing_document_number_unique',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'operation_key'],
                name='billing_document_operation_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(tier__in=TIERS),
                name='billing_document_tier_known',
            ),
            models.CheckConstraint(
                condition=models.Q(total_incl_tax__gte=0, balance_due__gte=0, overpaid_at_issue__gte=0),
                name='billing_document_amounts_not_negative',
            ),
            # An unregistered seller may not state GST. Repeated as a database
            # rule as well as a service one so a bulk write cannot leave a
            # document claiming tax nobody was entitled to charge.
            models.CheckConstraint(
                condition=models.Q(taxable_supply=True) | models.Q(tax_total=0, seller_gst_number=''),
                name='billing_document_untaxed_when_unregistered',
            ),
        ]

    def __str__(self):
        return self.document_number

    def clean(self):
        """Refuse a document whose order or customer is another workspace's."""
        super().clean()
        errors = {}
        if self.order_id and self.order.workspace_id != self.workspace_id:
            errors['order'] = 'The order belongs to a different workspace.'
        if self.customer_id and self.customer.workspace_id != self.workspace_id:
            errors['customer'] = 'The customer belongs to a different workspace.'
        registration = self.seller_registration
        if registration is not None and registration.workspace_id != self.workspace_id:
            errors['seller_registration'] = 'The arrangement belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)


class SupplyDocumentLine(models.Model):
    """One order line's worth of supply on a document.

    The line names how many commercial positions of its order line it covers
    and carries their exact money; the positions themselves are in
    `SupplyDocumentCoverage`. Nothing is apportioned or averaged here — the
    amounts are the sum of the positions, which is why a document always adds
    back up to the order it came from.
    """

    document = models.ForeignKey(
        SupplyDocument, on_delete=models.PROTECT, related_name='lines',
    )
    order_line = models.ForeignKey(
        'sales.SalesOrderLine', on_delete=models.PROTECT,
        related_name='supply_document_lines',
    )
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4)
    tax_treatment = models.CharField(max_length=16, blank=True, default='')
    gross_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    discount_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'order_line'],
                name='billing_document_line_order_line_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='billing_document_line_quantity_positive',
            ),
        ]

    def __str__(self):
        return f'{self.document_id}: {self.description}'


class SupplyDocumentCoverage(models.Model):
    """One commercial position of an order line, covered by one document line.

    The position is the same one-based index `FulfillmentLine.commercial_position`
    uses, so a document and a dispatch describe the same item the same way.
    `fulfillment_line` is set when the position had already been dispatched
    when the document was issued and empty when it had not, which is the whole
    of the difference between invoicing after delivery and invoicing ahead of
    it.
    """

    document_line = models.ForeignKey(
        SupplyDocumentLine, on_delete=models.PROTECT, related_name='coverage',
    )
    commercial_position = models.PositiveIntegerField()
    fulfillment_line = models.ForeignKey(
        'sales.FulfillmentLine', on_delete=models.PROTECT, null=True, blank=True,
        related_name='supply_document_coverage',
    )

    class Meta:
        ordering = ['commercial_position', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['document_line', 'commercial_position'],
                name='billing_coverage_position_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(commercial_position__gte=1),
                name='billing_coverage_position_positive',
            ),
        ]

    def __str__(self):
        return f'position {self.commercial_position}'


class SupplyCorrection(ImmutableCommerceModel, PartySnapshotModel):
    """A credit or debit note against one issued document.

    Amounts are stored as positive magnitudes and `correction_type` says which
    way they run. A signed column would let a credit of minus a negative exist,
    and the direction is the first thing anybody reading a correction needs.

    `reason_code` is required and closed. Change 4 asks a correction to state
    why it happened, and "adjustment" written in a free-text box is not a
    reason anybody can report on later; the free-text `reason` is for the
    detail on top of it.
    """

    class CorrectionType(models.TextChoices):
        """Which way a correction moves the value of the original supply."""

        CREDIT = 'credit', 'Credit'
        DEBIT = 'debit', 'Debit'

    class Reason(models.TextChoices):
        """Why an issued document needed correcting."""

        RETURN = 'return', 'Goods returned'
        DISCOUNT = 'discount', 'Discount or price adjustment'
        WRONG_RATE = 'wrong_rate', 'Wrong GST rate or treatment'
        CANCELLATION = 'cancellation', 'Supply cancelled'
        PARTIAL_CREDIT = 'partial_credit', 'Part of the supply credited'
        OTHER = 'other', 'Other'

    document_number = models.CharField(max_length=32)
    document = models.ForeignKey(
        SupplyDocument, on_delete=models.PROTECT, related_name='corrections',
    )
    correction_type = models.CharField(max_length=16, choices=CorrectionType.choices)
    reason_code = models.CharField(max_length=24, choices=Reason.choices)
    reason = models.TextField()
    corrected_on = models.DateField(
        help_text='The workspace-local business date the correction was made.',
    )
    sales_return = models.ForeignKey(
        'sales.SalesReturn', on_delete=models.PROTECT, null=True, blank=True,
        related_name='supply_corrections',
    )
    refund = models.ForeignKey(
        'sales.Refund', on_delete=models.PROTECT, null=True, blank=True,
        related_name='supply_corrections',
    )
    currency_code = models.CharField(max_length=3)
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['corrected_on', 'pk']
        indexes = [
            models.Index(
                fields=['workspace', 'corrected_on'],
                name='billing_correction_date_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'document_number'],
                name='billing_correction_number_unique',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'operation_key'],
                name='billing_correction_operation_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(total_incl_tax__gte=0, subtotal_ex_tax__gte=0, tax_total__gte=0),
                name='billing_correction_amounts_not_negative',
            ),
        ]

    def __str__(self):
        return self.document_number

    def clean(self):
        """Refuse a correction pointing outside its own workspace or document."""
        super().clean()
        errors = {}
        if self.document_id and self.document.workspace_id != self.workspace_id:
            errors['document'] = 'The document belongs to a different workspace.'
        if self.sales_return_id and self.sales_return.order_id != self.document.order_id:
            errors['sales_return'] = 'The return belongs to a different order.'
        if self.refund_id and self.refund.order_id != self.document.order_id:
            errors['refund'] = 'The refund belongs to a different order.'
        if errors:
            raise ValidationError(errors)


class SupplyCorrectionLine(models.Model):
    """How much of one document line a correction moves.

    `quantity` is how many of the line's positions the correction accounts for,
    and is left empty for a correction that changes only value — a discount
    against a supply nobody is giving back has no quantity.
    """

    correction = models.ForeignKey(
        SupplyCorrection, on_delete=models.PROTECT, related_name='lines',
    )
    document_line = models.ForeignKey(
        SupplyDocumentLine, on_delete=models.PROTECT, related_name='correction_lines',
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4)
    tax_treatment = models.CharField(max_length=16, blank=True, default='')
    subtotal_ex_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    tax_total = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )
    total_incl_tax = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
    )

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['correction', 'document_line'],
                name='billing_correction_line_unique',
            ),
            models.CheckConstraint(
                condition=models.Q(total_incl_tax__gte=0),
                name='billing_correction_line_not_negative',
            ),
        ]

    def __str__(self):
        return f'{self.correction_id}: line {self.document_line_id}'
