"""
Config for the seeds app
"""
from django.apps import AppConfig


class SeedsConfig(AppConfig):
    """
    Specific config for seeds app
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "seeds"
