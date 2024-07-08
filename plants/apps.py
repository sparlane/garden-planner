"""
App Configuration for Plants
"""
from django.apps import AppConfig


class PlantsConfig(AppConfig):
    """
    Configuration for the Plants App
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "plants"
