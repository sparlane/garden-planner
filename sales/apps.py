"""Application configuration for customer sales."""

from django.apps import AppConfig


class SalesConfig(AppConfig):
    """Configure the sales application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sales'
