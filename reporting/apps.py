"""Application configuration for centralized Nursery reports."""

from django.apps import AppConfig


class ReportingConfig(AppConfig):
    """Configure the report service application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reporting'
