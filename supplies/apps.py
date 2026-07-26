"""Django application configuration for supplies."""
from django.apps import AppConfig


class SuppliesConfig(AppConfig):
    """Configure the supplies application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "supplies"
