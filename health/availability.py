"""Authoritative current quarantine projections and locking checks."""

from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef

from .models import QuarantineAction, QuarantineCase, QuarantineMember


CLOSING_ACTIONS = (
    QuarantineAction.Action.RELEASE,
    QuarantineAction.Action.CULL,
)


def active_cases(workspace):
    """Return cases with an opening action and no release or cull action."""
    closed = QuarantineAction.objects.filter(
        case=OuterRef('pk'), action__in=CLOSING_ACTIONS,
    )
    return QuarantineCase.objects.filter(
        workspace=workspace,
        actions__action=QuarantineAction.Action.QUARANTINE,
    ).exclude(Exists(closed)).distinct()


def case_is_active(case):
    """Return whether one case still constrains availability."""
    return active_cases(case.workspace).filter(pk=case.pk).exists()


def with_quarantine(queryset, target_type='plant'):
    """Annotate a plant or cohort queryset with current quarantine state."""
    if target_type not in {'plant', 'cohort'}:
        raise ValueError('Quarantine projection supports plants or cohorts.')
    closed_cases = QuarantineAction.objects.filter(
        action__in=CLOSING_ACTIONS,
    ).values('case_id')
    memberships = QuarantineMember.objects.filter(
        case__workspace_id=OuterRef('workspace_id'),
        case__actions__action=QuarantineAction.Action.QUARANTINE,
        **{f'{target_type}_id': OuterRef('pk')},
    ).exclude(case_id__in=closed_cases)
    return queryset.annotate(quarantined=Exists(memberships))


def is_quarantined(target):
    """Check current quarantine state for one plant or cohort."""
    field = 'plant' if target._meta.model_name == 'specificplant' else 'cohort'  # pylint: disable=protected-access
    return QuarantineMember.objects.filter(
        case__in=active_cases(target.workspace),
        **{field: target},
    ).exists()


def require_available(targets, *, lock=False):
    """Reject a set containing quarantined stock after optional row locking."""
    rows = list(targets.select_for_update().order_by('pk') if lock else targets)
    blocked = [row.pk for row in rows if is_quarantined(row)]
    if blocked:
        raise ValidationError({
            'targets': f'Quarantined stock is unavailable: {blocked}.',
        })
    return rows
