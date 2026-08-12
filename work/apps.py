"""Application configuration for nursery work scheduling."""

from django.apps import AppConfig


class WorkConfig(AppConfig):
    """Configure the work scheduling application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'work'

    def ready(self):
        """Register workspace-profile integration after models are loaded."""
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import
