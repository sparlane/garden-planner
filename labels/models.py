"""Workspace-scoped label identities and immutable print history."""

# The timestamp, ordering, and workspace-unique-name fields intentionally
# follow the same catalog convention as inventory and location records.
# pylint: disable=duplicate-code

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from workspaces.models import WorkspaceOwnedModel


class LabelIdentity(WorkspaceOwnedModel):
    """One stable label identity for one object, even if that object is removed."""

    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    target_object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey('target_content_type', 'target_object_id')
    target_snapshot = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['target_content_type_id', 'target_object_id']
        constraints = [
            models.UniqueConstraint(
                fields=['workspace', 'target_content_type', 'target_object_id'],
                name='label_identity_workspace_target_unique',
            ),
        ]

    def __str__(self):
        return f'{self.target_content_type.app_label}.{self.target_content_type.model}:{self.target_object_id}'


class LabelCode(WorkspaceOwnedModel):
    """One issued code; retirement changes its status but never its identity."""

    class Status(models.TextChoices):
        """Whether this exact printed value may still be used."""

        ACTIVE = 'active', 'Active'
        REPLACED = 'replaced', 'Replaced'
        VOID = 'void', 'Void'

    identity = models.ForeignKey(LabelIdentity, on_delete=models.PROTECT, related_name='codes')
    code = models.CharField(max_length=32)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    retirement_reason = models.TextField(blank=True, default='')
    replacement = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='replaces',
    )

    class Meta:
        ordering = ['-issued_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'code'], name='label_code_workspace_unique'),
            models.UniqueConstraint(
                fields=['identity'],
                condition=models.Q(status='active'),
                name='label_code_one_active_per_identity',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.code != self.code.strip().upper():
            errors['code'] = 'Label codes must be normalized uppercase text.'
        if self.identity_id and self.identity.workspace_id != self.workspace_id:
            errors['identity'] = 'The identity belongs to a different workspace.'
        if self.replacement_id:
            if self.replacement.identity_id != self.identity_id:
                errors['replacement'] = 'A replacement must identify the same object.'
            elif self.replacement.workspace_id != self.workspace_id:
                errors['replacement'] = 'The replacement belongs to a different workspace.'
        if self.status == self.Status.ACTIVE:
            if self.retired_at or self.retirement_reason or self.replacement_id:
                errors['status'] = 'An active code cannot carry retirement details.'
        elif not self.retired_at or not self.retirement_reason.strip():
            errors['retirement_reason'] = 'A retired code requires a date and reason.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class LabelTemplate(WorkspaceOwnedModel):
    """A reusable physical layout and field selection."""

    class Format(models.TextChoices):
        """Machine-readable symbol rendered on the label."""

        QR = 'qr', 'QR code'
        CODE128 = 'code128', 'Code 128'

    class PayloadMode(models.TextChoices):
        """Information encoded inside a QR or linear barcode."""

        CODE = 'code', 'Bare code'
        URL = 'url', 'Application deep link'

    class Layout(models.TextChoices):
        """Physical medium a template arranges labels upon."""

        SINGLE = 'single', 'Single label'
        SHEET = 'sheet', 'Sheet'
        ROLL = 'roll', 'Roll printer'

    name = models.CharField(max_length=120)
    format = models.CharField(max_length=12, choices=Format.choices)
    payload_mode = models.CharField(max_length=8, choices=PayloadMode.choices, default=PayloadMode.CODE)
    layout = models.CharField(max_length=12, choices=Layout.choices)
    fields = models.JSONField(default=list)
    dimensions = models.JSONField(default=dict)
    built_in = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'name'], name='label_template_workspace_name_unique'),
        ]

    def __str__(self):
        return self.name


class LabelPrintJob(WorkspaceOwnedModel):
    """An immutable snapshot of labels prepared for physical printing."""

    template = models.ForeignKey(LabelTemplate, on_delete=models.PROTECT, null=True, related_name='print_jobs')
    template_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created = models.DateTimeField(auto_now_add=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created', '-pk']


class LabelPrintItem(models.Model):
    """One label and payload frozen into a print job."""

    job = models.ForeignKey(LabelPrintJob, on_delete=models.PROTECT, related_name='items')
    identity = models.ForeignKey(LabelIdentity, on_delete=models.PROTECT, related_name='print_items')
    code = models.ForeignKey(LabelCode, on_delete=models.PROTECT, related_name='print_items')
    position = models.PositiveIntegerField()
    target_snapshot = models.JSONField(default=dict)
    payload = models.TextField()
    is_reprint = models.BooleanField(default=False)

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['job', 'position'], name='label_print_item_job_position_unique'),
        ]
