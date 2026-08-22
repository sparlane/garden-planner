"""Transactional services for recording GST arrangements.

Nothing outside this module creates a `GstRegistration`. The model refuses an
update, so the only decisions left are which dated row to append and what to
tell the operator about the consequences, and both belong here.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from workspaces.models import get_current_workspace

from .models import GstPeriodClosure, GstRegistration, TaxTreatmentCorrection


@transaction.atomic
def record_registration(workspace, user, **values):
    """Append one dated GST arrangement and return it.

    Eligibility is deliberately not enforced. A workspace whose turnover has
    outgrown the payments basis is required to change basis, but it does not
    stop being on the payments basis the moment it crosses the threshold, and
    refusing to record what is actually true would leave its returns
    unproducible. The consequences are reported as warnings instead.
    """
    registration = GstRegistration(
        workspace=workspace,
        created_by=user if user is not None and user.is_authenticated else None,
        **values,
    )
    registration.save()
    return registration


@transaction.atomic
def supersede_registration(registration, user, **values):
    """Replace one recorded arrangement with a corrected one.

    The mistake stays in the table. A correction that simply overwrote the row
    would leave a filed return referring to an arrangement no longer visible
    anywhere, which is the failure this whole app is shaped to avoid.
    """
    if hasattr(registration, 'superseded_by'):
        raise ValidationError(
            {'supersedes': 'That arrangement has already been superseded.'},
        )
    return record_registration(
        registration.workspace,
        user,
        supersedes=registration,
        **values,
    )


def current_registration(workspace=None):
    """Return the arrangement in force today, or None if unregistered."""
    from .periods import local_date, registration_in_force  # pylint: disable=import-outside-toplevel
    from django.utils import timezone  # pylint: disable=import-outside-toplevel

    workspace = workspace or get_current_workspace()
    today = local_date(workspace, timezone.now())
    return registration_in_force(workspace, today)


#: The treatments a zero-rated-looking line may be moved between. Standard is
#: absent on purpose: moving to or from it would change the tax on the line,
#: which is a change to the price the customer agreed, not a classification.
CORRECTABLE_TREATMENTS = ('zero_rated', 'exempt', 'out_of_scope', 'unclassified')


@transaction.atomic
def correct_tax_treatment(line, treatment, user, reason):
    """Reclassify one confirmed order line's GST treatment, with an audit row.

    The write uses a queryset update rather than `save`, deliberately: the
    model refuses every save on a confirmed line, and that guard is protecting
    the agreed price. Nothing here changes an amount — the line's rate is zero
    on both sides of the change — so the guard is not being weakened, only
    stepped around for the one field it was never about.

    The already-posted fulfillment and refund lines are updated in the same
    transaction. They are the record of record for a return, so leaving them
    behind would make the correction invisible to every report that matters.
    """
    from sales.models import FulfillmentLine, RefundLine, SalesOrderLine  # pylint: disable=import-outside-toplevel

    if treatment not in CORRECTABLE_TREATMENTS:
        raise ValidationError({
            'treatment': (
                'Only zero-rated, exempt, out-of-scope and unclassified can be '
                'corrected. Changing to or from standard-rated would change the '
                'tax on an agreed price.'
            ),
        })
    if line.tax_rate != 0:
        raise ValidationError({
            'sales_order_line': (
                'That line carries a tax rate, so its treatment is part of the '
                'price rather than a classification.'
            ),
        })
    if line.tax_treatment == treatment:
        raise ValidationError(
            {'treatment': 'That line already carries that treatment.'},
        )
    correction = TaxTreatmentCorrection(
        workspace=line.order.workspace,
        sales_order_line=line,
        previous_treatment=line.tax_treatment,
        treatment=treatment,
        created_by=user if user is not None and user.is_authenticated else None,
        reason=reason,
    )
    correction.save()
    SalesOrderLine.objects.filter(pk=line.pk).update(tax_treatment=treatment)
    FulfillmentLine.objects.filter(allocation__line=line).update(tax_treatment=treatment)
    RefundLine.objects.filter(
        fulfillment_line__allocation__line=line,
    ).update(tax_treatment=treatment)
    line.tax_treatment = treatment
    return correction


@transaction.atomic
def close_period(workspace, user, period, filed_totals, notes=''):
    """Record that one taxable period has been reported, with its filed figures.

    The figures are stored rather than derived, which is the one deliberate
    exception to how this app works. Everything else is re-read from the
    commerce records so it can never disagree with them — but a period already
    filed is precisely the thing that must not silently follow a late
    correction, and keeping what was filed is how the drift becomes visible.
    """
    closure = GstPeriodClosure(
        workspace=workspace,
        period_start=period.start,
        period_end=period.end,
        registration_id=period.registration_id,
        basis=period.basis,
        filing_frequency=period.frequency,
        filed_totals=filed_totals,
        notes=notes,
        closed_by=user if user is not None and user.is_authenticated else None,
    )
    closure.save()
    return closure


def closures_by_label(workspace):
    """Return every filed period, keyed by the label the report uses."""
    return {
        closure.label: closure
        for closure in GstPeriodClosure.objects.filter(workspace=workspace)
    }
