"""Django application configuration for seed trays."""
from django.apps import AppConfig


class SeedtraysConfig(AppConfig):
    """Configure the seed-tray application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "seedtrays"
