"""Application configuration for private attachments."""

from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    """Configure stored image attachments."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'attachments'
