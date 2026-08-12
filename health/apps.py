"""Application configuration for plant health."""

from django.apps import AppConfig


class HealthConfig(AppConfig):
    """Configure the plant-health application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'health'

    def ready(self):
        """Register Nursery catalog defaults after models are loaded."""
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import
