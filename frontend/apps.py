"""
App Config for FrontEnd
"""
from django.apps import AppConfig


class FrontendConfig(AppConfig):
    """
    Frontend App Config
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "frontend"
