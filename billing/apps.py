"""Application configuration for taxable supply and correction documents."""

from django.apps import AppConfig


class BillingConfig(AppConfig):
    """Configure issued supply documents and their corrections."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'billing'
