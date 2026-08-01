"""Resolve the deployment's configured workspace."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .models import Workspace


def get_current_workspace():
    """Return the single workspace configured for this deployment."""
    workspace_id = settings.CURRENT_WORKSPACE_ID
    try:
        return Workspace.objects.get(pk=workspace_id)
    except Workspace.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f'CURRENT_WORKSPACE_ID={workspace_id} does not identify a workspace.'
        ) from exc
