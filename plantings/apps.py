"""
App Config for Plantings
"""
from django.apps import AppConfig


class PlantingsConfig(AppConfig):
    """
    Planting App Config
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "plantings"

    def ready(self):
        """Register workspace-default hooks after the app registry is ready."""
        # Register signal handlers only after Django has populated the app registry.
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import
