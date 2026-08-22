"""Append-only record of a workspace's GST registration and accounting basis.

Registration is a history, not a setting. A nursery registers, changes its
filing frequency, moves from the payments basis to the invoice basis, and may
eventually deregister; every one of those is true from a date, and a return
already filed under the old arrangement must keep reading the way it was
filed. So none of it belongs on `Workspace`, which is a mutable singleton — a
basis field there would be rewritten in place, and task 117's requirement to
handle a change of basis "without rewriting historical rows" would be
impossible by construction rather than merely unimplemented.

What is stored instead is one row per change, carrying `effective_from` and
nothing that closes it. The row in force on any date is the latest one on or
before it; a deregistration is a row with `registered` false, so a gap in
registration is a fact the history states rather than an absence anybody has
to infer. Correcting a row means superseding it, which leaves the mistake and
the correction both readable.

Rows are immutable in the same style as `sales.ImmutableCommerceModel` and
`costing.CostAllocation`.
"""

# The actor and timestamp columns are the same six lines in every app that
# records who did something; `inventory` and `costing` carry the identical
# declaration for the identical reason.
# pylint: disable=duplicate-code

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from workspaces.models import WorkspaceOwnedModel

from .ird import normalize_ird_number, validate_ird_number


class GstRegistration(WorkspaceOwnedModel):
    """One dated change to how this workspace accounts for GST."""

    class Basis(models.TextChoices):
        """When a supply is brought to account for GST.

        Payments recognises on money received and paid. Invoice recognises on
        the earlier of an invoice issued and a payment received. Hybrid is
        invoice for output tax and payments for input tax, which is why it is
        one choice here rather than two independent fields: nothing else in
        the tax rules combines them freely.
        """

        PAYMENTS = 'payments', 'Payments'
        INVOICE = 'invoice', 'Invoice'
        HYBRID = 'hybrid', 'Hybrid'

    class Frequency(models.TextChoices):
        """How often a return covering one taxable period is filed."""

        MONTHLY = 'monthly', 'Monthly'
        TWO_MONTHLY = 'two_monthly', 'Two-monthly'
        SIX_MONTHLY = 'six_monthly', 'Six-monthly'

    registered = models.BooleanField(
        help_text=(
            'Whether the workspace is GST registered from this date. A false '
            'row records a deregistration or cessation.'
        ),
    )
    effective_from = models.DateField(
        help_text='The first day this arrangement applies to.',
    )
    gst_number = models.CharField(
        max_length=11,
        blank=True,
        default='',
        help_text='The nine-digit IRD/GST number the registration is held under.',
    )
    basis = models.CharField(
        max_length=16,
        choices=Basis.choices,
        blank=True,
        default='',
        help_text='The accounting basis in force from this date.',
    )
    filing_frequency = models.CharField(
        max_length=16,
        choices=Frequency.choices,
        blank=True,
        default='',
        help_text='How often a return is filed from this date.',
    )
    period_anchor_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text=(
            'A month a taxable period ends in, from 1 through 12. This is how '
            'Inland Revenue names a cycle: two-monthly filing ends in either '
            'the odd or the even months, and six-monthly in March/September, '
            'April/October, or May/November. Ignored by monthly filing.'
        ),
    )
    taxable_activity_start = models.DateField(
        null=True,
        blank=True,
        help_text=(
            'When the taxable activity itself began, which may be well before '
            'the registration and is what the turnover threshold is measured '
            'from.'
        ),
    )
    reason = models.TextField(
        blank=True,
        default='',
        help_text='Why the arrangement changed, in the operator\'s own words.',
    )
    notes = models.TextField(blank=True, default='')
    supersedes = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='superseded_by',
        help_text=(
            'The row this one corrects. A superseded row stays in the table '
            'and stops contributing to what was in force.'
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['effective_from', 'pk']
        indexes = [
            models.Index(
                fields=['workspace', 'effective_from'],
                name='gst_registration_date_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    registered=True,
                    basis__in=('payments', 'invoice', 'hybrid'),
                    filing_frequency__in=('monthly', 'two_monthly', 'six_monthly'),
                    period_anchor_month__isnull=False,
                ) | models.Q(
                    registered=False,
                    gst_number='',
                    basis='',
                    filing_frequency='',
                    period_anchor_month__isnull=True,
                ),
                name='gst_registration_state_fields',
            ),
            models.CheckConstraint(
                condition=models.Q(period_anchor_month__isnull=True) | models.Q(
                    period_anchor_month__gte=1, period_anchor_month__lte=12,
                ),
                name='gst_registration_anchor_range',
            ),
        ]

    def __str__(self):
        state = 'registered' if self.registered else 'not registered'
        return f'{state} from {self.effective_from.isoformat()}'

    def clean(self):
        super().clean()
        errors = {}
        if self.registered:
            errors.update(self._registered_field_errors())
        else:
            errors.update(self._deregistered_field_errors())
        if self.effective_from is not None:
            errors.update(self._ordering_errors())
            errors.update(self._live_date_errors())
            activity_start = self.taxable_activity_start
            if activity_start is not None and activity_start > self.effective_from:
                errors['taxable_activity_start'] = (
                    'A taxable activity cannot start after the registration it supports.'
                )
        if self.supersedes_id and self.supersedes.workspace_id != self.workspace_id:
            errors['supersedes'] = 'The superseded row belongs to a different workspace.'
        if errors:
            raise ValidationError(errors)

    def _registered_field_errors(self):
        """Return the errors for a row that claims the workspace is registered."""
        errors = {}
        if not self.gst_number:
            errors['gst_number'] = 'A registered workspace needs its GST number.'
        else:
            try:
                validate_ird_number(self.gst_number)
            except ValidationError as exc:
                errors['gst_number'] = exc.messages
        if not self.basis:
            errors['basis'] = 'Choose the accounting basis this registration uses.'
        if not self.filing_frequency:
            errors['filing_frequency'] = 'Choose how often a return is filed.'
        if self.period_anchor_month is None:
            errors['period_anchor_month'] = 'Choose a month the taxable period ends in.'
        return errors

    def _deregistered_field_errors(self):
        """Return the errors for a row that records a deregistration.

        A deregistration carries no configuration at all. The number, basis and
        frequency that applied until this date are still readable on the row
        before it, and repeating them here would make it ambiguous whether they
        were still in force afterwards.
        """
        errors = {}
        for field, message in (
            ('gst_number', 'A deregistration carries no GST number.'),
            ('basis', 'A deregistration carries no accounting basis.'),
            ('filing_frequency', 'A deregistration carries no filing frequency.'),
        ):
            if getattr(self, field):
                errors[field] = message
        if self.period_anchor_month is not None:
            errors['period_anchor_month'] = 'A deregistration carries no period anchor.'
        return errors

    def _ordering_errors(self):
        """Refuse a new arrangement dated at or before one already recorded.

        This is what makes "no rewriting of history" structural rather than a
        promise. A first registration may still be backdated as far as the
        operator likes, because there is nothing later to contradict; once a
        second arrangement exists, changing what applied in between means
        superseding the row that said so.
        """
        if self.supersedes_id:
            return {}
        latest = (
            type(self).objects
            .filter(workspace_id=self.workspace_id, superseded_by__isnull=True)
            .exclude(pk=self.pk)
            .order_by('-effective_from', '-pk')
            .first()
        )
        if latest is not None and self.effective_from <= latest.effective_from:
            return {
                'effective_from': (
                    'A later arrangement is already recorded from '
                    f'{latest.effective_from.isoformat()}. Supersede that row '
                    'instead of dating a new one before it.'
                ),
            }
        return {}

    def _live_date_errors(self):
        """Refuse a second live arrangement sharing one date.

        The history has to be totally ordered, or the row in force on a date is
        a coin toss between two rows. This cannot be a database constraint: the
        condition needs `superseded_by`, a reverse relation, and a constraint
        may only reference local columns.
        """
        clash = (
            type(self).objects
            .filter(
                workspace_id=self.workspace_id,
                effective_from=self.effective_from,
                superseded_by__isnull=True,
            )
            .exclude(pk=self.pk)
            .exclude(pk=self.supersedes_id)
            .exists()
        )
        if clash:
            return {
                'effective_from': (
                    'Another arrangement already applies from that date. '
                    'Supersede it rather than recording a second one.'
                ),
            }
        return {}

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                'GST registration records are immutable; supersede them instead.',
            )
        if self.gst_number:
            self.gst_number = normalize_ird_number(self.gst_number)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('GST registration records cannot be deleted.')


class TaxTreatmentCorrection(WorkspaceOwnedModel):
    """An audited reclassification of one confirmed order line's GST treatment.

    `SalesOrderLine.clean` refuses every save once its order leaves quote or
    draft, which is right: the price a customer agreed is not something to
    edit afterwards. But it also means a line already confirmed can never be
    reclassified, and every line that existed before this feature landed is
    `unclassified` — so the zero-rated box of a GST return would be permanently
    empty for exactly the workspaces that have history.

    The narrow exception this record covers is a line whose rate is zero, where
    moving between zero-rated, exempt and out-of-scope changes which box the
    figure is reported in and moves no money at all. The guard exists to
    protect the price; nothing here touches it. A line carrying a rate is
    refused outright, and every correction is recorded with its actor and its
    reason.
    """

    sales_order_line = models.ForeignKey(
        'sales.SalesOrderLine',
        on_delete=models.PROTECT,
        related_name='tax_treatment_corrections',
    )
    previous_treatment = models.CharField(max_length=16)
    treatment = models.CharField(max_length=16)
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created', 'pk']
        indexes = [
            models.Index(
                fields=['workspace', 'sales_order_line'],
                name='gst_treatment_line_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(treatment=models.F('previous_treatment')),
                name='gst_treatment_correction_changes_something',
            ),
        ]

    def __str__(self):
        return f'line {self.sales_order_line_id}: {self.previous_treatment} to {self.treatment}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Tax treatment corrections are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Tax treatment corrections cannot be deleted.')
