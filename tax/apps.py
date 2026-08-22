"""Application configuration for New Zealand tax controls."""

from django.apps import AppConfig


class TaxConfig(AppConfig):
    """Configure GST registration, accounting basis, and period reporting."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tax'
