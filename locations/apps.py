"""Application configuration for the shared physical location catalog."""

from django.apps import AppConfig


class LocationsConfig(AppConfig):
    """Configure the catalog of places a workspace physically uses."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'locations'
