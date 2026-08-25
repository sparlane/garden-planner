"""Database models for garden supplies."""
from django.core.exceptions import ValidationError
from django.db import models

from tax.ird import normalize_ird_number, validate_ird_number
from workspaces.models import WorkspaceOwnedModel


class Supplier(WorkspaceOwnedModel):
    """
    A seed supplier
    """
    class GstStatus(models.TextChoices):
        """What the operator knows about this supplier's NZ GST status."""

        REGISTERED = 'registered', 'GST registered'
        UNREGISTERED = 'unregistered', 'Not GST registered'
        UNKNOWN = 'unknown', 'Unknown'

    name = models.CharField(max_length=1024)
    address = models.TextField(blank=True, default='')
    gst_status = models.CharField(
        max_length=16,
        choices=GstStatus.choices,
        default=GstStatus.UNKNOWN,
    )
    gst_number = models.CharField(max_length=16, blank=True, default='')
    website = models.CharField(max_length=1024, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    is_system_default = models.BooleanField(
        default=False,
        editable=False,
        help_text=(
            'Stands in for a supplier a Basic Garden workflow left unnamed. '
            'See supplies.defaults.ensure_default_supplier.'
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['workspace'],
                condition=models.Q(is_system_default=True),
                name='supplier_one_system_default_per_workspace',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        """Keep the GST number consistent with the stated registration."""
        super().clean()
        if self.gst_status != self.GstStatus.REGISTERED:
            if self.gst_number:
                raise ValidationError({
                    'gst_number': 'Only a GST-registered supplier has a GST number.',
                })
            return
        if not self.gst_number:
            raise ValidationError({
                'gst_number': 'A GST-registered supplier needs its GST number.',
            })
        try:
            self.gst_number = normalize_ird_number(self.gst_number)
            validate_ird_number(self.gst_number)
        except ValidationError as exc:
            raise ValidationError({'gst_number': exc.messages}) from exc

    def save(self, *args, **kwargs):
        """Validate direct ORM writes as well as REST writes."""
        self.full_clean()
        super().save(*args, **kwargs)
