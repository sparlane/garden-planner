"""Application configuration for tax-oriented bookkeeping."""

from django.apps import AppConfig


class BookkeepingConfig(AppConfig):
    """Configure the bookkeeping domain."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bookkeeping'

    def ready(self):
        """Register cross-app deletion protection after every model is loaded."""
        # Register signal handlers only after Django has populated the app registry.
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import
