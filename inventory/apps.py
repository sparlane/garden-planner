"""Application configuration for inventory."""

from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Configure the shared inventory catalog application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'
