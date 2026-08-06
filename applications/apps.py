"""Application configuration for input applications."""

from django.apps import AppConfig


class ApplicationsConfig(AppConfig):
    """Configure the audited input application documents."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'applications'
