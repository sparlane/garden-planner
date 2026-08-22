"""Transactional services for recording GST arrangements.

Nothing outside this module creates a `GstRegistration`. The model refuses an
update, so the only decisions left are which dated row to append and what to
tell the operator about the consequences, and both belong here.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from workspaces.models import get_current_workspace

from .models import GstRegistration


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
