"""Application configuration for labels."""

from django.apps import AppConfig


class LabelsConfig(AppConfig):
    """Register automatic identity issuance for label-worthy records."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'labels'

    def ready(self):
        from . import signals  # pylint: disable=import-outside-toplevel,unused-import
