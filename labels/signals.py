"""Automatic label issuance for every currently supported target type."""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from garden.models import GardenArea
from locations.models import Location
from plantings.models import ProductionBatch, SpecificPlant
from seedtrays.models import SeedTray
from workspaces.models import Workspace

from .models import LabelIdentity
from .services import ensure_default_templates, ensure_identity, target_key


SUPPORTED_MODELS = (SpecificPlant, SeedTray, ProductionBatch, Location, GardenArea)


@receiver(post_save, sender=Workspace)
def create_workspace_label_templates(sender, instance, created, raw=False, **kwargs):  # pylint: disable=unused-argument
    """Give every new workspace the same useful starting print layouts."""
    if created and not raw:
        ensure_default_templates(instance)


@receiver(post_save, sender=SpecificPlant)
@receiver(post_save, sender=SeedTray)
@receiver(post_save, sender=ProductionBatch)
@receiver(post_save, sender=Location)
@receiver(post_save, sender=GardenArea)
def issue_label_identity(sender, instance, created, raw=False, **kwargs):  # pylint: disable=unused-argument
    """Issue an identity after the target has a durable primary key."""
    if created and not raw:
        ensure_identity(instance, getattr(instance, 'created_by', None))


@receiver(pre_delete, sender=SpecificPlant)
@receiver(pre_delete, sender=SeedTray)
@receiver(pre_delete, sender=ProductionBatch)
@receiver(pre_delete, sender=Location)
@receiver(pre_delete, sender=GardenArea)
def retire_deleted_target(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Keep scans explicit after the underlying target is deleted."""
    content_type, _key = target_key(instance)
    LabelIdentity.objects.filter(
        workspace=instance.workspace,
        target_content_type=content_type,
        target_object_id=instance.pk,
    ).update(active=False)
