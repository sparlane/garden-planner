"""Django application configuration for workspaces."""

from django.apps import AppConfig


class WorkspacesConfig(AppConfig):
    """Configure the workspace application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspaces'
