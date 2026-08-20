"""Database models for garden supplies."""
from django.db import models

from workspaces.models import WorkspaceOwnedModel


class Supplier(WorkspaceOwnedModel):
    """
    A seed supplier
    """
    name = models.CharField(max_length=1024)
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
