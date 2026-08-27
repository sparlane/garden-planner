"""Deletion guard for source records retained by finalized tax working papers."""

import re

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import TaxRetentionRecord


def _source_type(instance):
    """Translate a Django model name to the stable report source spelling."""
    name = type(instance).__name__
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


@receiver(pre_delete)
def prevent_retained_source_deletion(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Block deletion while a finalized tax source is retained or held."""
    workspace_id = getattr(instance, 'workspace_id', None)
    if workspace_id is None or isinstance(instance, TaxRetentionRecord):
        return
    retained = TaxRetentionRecord.objects.filter(
        workspace_id=workspace_id,
        source_type=_source_type(instance), source_id=str(instance.pk),
    ).filter(legal_hold=True).exists()
    if not retained:
        retained = TaxRetentionRecord.objects.filter(
            workspace_id=workspace_id,
            source_type=_source_type(instance), source_id=str(instance.pk),
            retain_until__gte=timezone.localdate(),
        ).exists()
    if retained:
        raise ValidationError('This tax source is inside its retention period or under legal hold.')
