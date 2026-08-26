"""Application configuration for purchasing."""

from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    """Register the purchasing domain."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'purchasing'
