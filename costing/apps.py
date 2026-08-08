"""Application configuration for the production-cost subledger."""

from django.apps import AppConfig


class CostingConfig(AppConfig):
    """Configure the append-only per-plant cost allocation ledger."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'costing'
