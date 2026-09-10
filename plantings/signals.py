"""Lifecycle hooks for Nursery workspace defaults."""

from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from workspaces.models import Workspace

from .models import GrowthStage, PlantGrade, SpecificPlantLocation


@receiver(pre_delete, sender=SpecificPlantLocation)
def preserve_fill_participant(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Deleting a participant would erase its share of the fill's media."""
    if instance.container_fill_id:
        raise ProtectedError('Container fill placement history cannot be deleted.', [instance])


@receiver(post_save, sender=Workspace)
def create_nursery_catalogs(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Give newly created or newly switched Nursery workspaces usable catalogs."""
    if instance.mode != Workspace.Mode.NURSERY:
        return
    stages = (
        ('struck', 14),
        ('rooted', 21),
        ('potted_on', 21),
        ('hardening', 7),
        ('sale_ready', None),
    )
    for order, (code, target_days) in enumerate(stages):
        GrowthStage.objects.get_or_create(
            workspace=instance, code=code,
            defaults={
                'name': code.replace('_', ' ').title(),
                'display_order': order,
                'target_days': target_days,
            },
        )
    for order, code in enumerate(('premium', 'standard', 'seconds')):
        PlantGrade.objects.get_or_create(
            workspace=instance, code=code,
            defaults={'name': code.title(), 'display_order': order},
        )
