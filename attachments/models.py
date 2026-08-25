"""Immutable private images attached to workspace-owned records."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from workspaces.models import WorkspaceOwnedModel

from .storage import private_attachment_storage


def _stored_image_path(instance, _filename):
    extension = 'png' if instance.content_type == 'image/png' else 'jpg'
    return f'originals/{instance.public_id}.{extension}'


def _stored_thumbnail_path(instance, _filename):
    extension = 'png' if instance.content_type == 'image/png' else 'jpg'
    return f'thumbnails/{instance.public_id}.{extension}'


class ImageAttachment(WorkspaceOwnedModel):
    """One sanitized image retained with exactly one immutable record."""

    class TargetType(models.TextChoices):
        """Stable API names for supported attachment owners."""

        PLANT = 'plant', 'Plant'
        NURSERY_OBSERVATION = 'nursery_observation', 'Nursery observation'
        HEALTH_OBSERVATION = 'health_observation', 'Health observation'
        HARVEST = 'harvest', 'Harvest'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    plant = models.ForeignKey(
        'plantings.SpecificPlant', on_delete=models.PROTECT,
        null=True, blank=True, related_name='image_attachments',
    )
    nursery_observation = models.ForeignKey(
        'plantings.NurseryObservation', on_delete=models.PROTECT,
        null=True, blank=True, related_name='image_attachments',
    )
    health_observation = models.ForeignKey(
        'health.HealthObservation', on_delete=models.PROTECT,
        null=True, blank=True, related_name='image_attachments',
    )
    harvest = models.ForeignKey(
        'plantings.Harvest', on_delete=models.PROTECT,
        null=True, blank=True, related_name='image_attachments',
    )
    original = models.FileField(
        storage=private_attachment_storage, upload_to=_stored_image_path,
        max_length=255,
    )
    thumbnail = models.FileField(
        storage=private_attachment_storage, upload_to=_stored_thumbnail_path,
        max_length=255,
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    captured_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        editable=False, related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    TARGET_FIELDS = {
        TargetType.PLANT: 'plant',
        TargetType.NURSERY_OBSERVATION: 'nursery_observation',
        TargetType.HEALTH_OBSERVATION: 'health_observation',
        TargetType.HARVEST: 'harvest',
    }

    class Meta:
        ordering = ['created', 'pk']
        constraints = [models.CheckConstraint(
            condition=(
                models.Q(
                    plant__isnull=False, nursery_observation__isnull=True,
                    health_observation__isnull=True, harvest__isnull=True,
                ) | models.Q(
                    plant__isnull=True, nursery_observation__isnull=False,
                    health_observation__isnull=True, harvest__isnull=True,
                ) | models.Q(
                    plant__isnull=True, nursery_observation__isnull=True,
                    health_observation__isnull=False, harvest__isnull=True,
                ) | models.Q(
                    plant__isnull=True, nursery_observation__isnull=True,
                    health_observation__isnull=True, harvest__isnull=False,
                )
            ),
            name='attachment_exactly_one_target',
        )]
        indexes = [
            models.Index(fields=['workspace', 'plant']),
            models.Index(fields=['workspace', 'nursery_observation']),
            models.Index(fields=['workspace', 'health_observation']),
            models.Index(fields=['workspace', 'harvest']),
        ]

    @property
    def target_type(self):
        """Return the stable name of the populated target relationship."""
        return next(
            name for name, field in self.TARGET_FIELDS.items()
            if getattr(self, f'{field}_id') is not None
        )

    @property
    def target_id(self):
        """Return the primary key of the populated target relationship."""
        return getattr(self, f'{self.TARGET_FIELDS[self.target_type]}_id')

    def clean(self):
        """Keep the attachment and its target inside one workspace."""
        super().clean()
        targets = [
            getattr(self, field) for field in self.TARGET_FIELDS.values()
            if getattr(self, f'{field}_id') is not None
        ]
        if len(targets) != 1:
            raise ValidationError({'target': 'Choose exactly one attachment target.'})
        if targets[0].workspace_id != self.workspace_id:
            raise ValidationError({'target': 'The target belongs to another workspace.'})

    def save(self, *args, **kwargs):
        """Create once; corrections retain the original attachment."""
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Image attachments are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent ordinary code paths from erasing retained evidence."""
        raise ValidationError('Image attachments cannot be deleted.')
