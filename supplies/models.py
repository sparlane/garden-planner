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

    def __str__(self):
        return self.name
