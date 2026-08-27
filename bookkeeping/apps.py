"""Application configuration for tax-oriented bookkeeping."""

from django.apps import AppConfig


class BookkeepingConfig(AppConfig):
    """Configure the bookkeeping domain."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookkeeping'
