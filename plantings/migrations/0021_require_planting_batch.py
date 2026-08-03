"""Require the production batch now that every sowing is linked."""

import django.db.models.deletion
from django.db import migrations, models


SOWING_MODELS = (
    'gardenrowdirectsowplanting',
    'gardensquaredirectsowplanting',
    'seedtrayplanting',
)


def require_batch(model_name):
    """Return the non-null batch relationship for one sowing model."""
    return migrations.AlterField(
        model_name=model_name,
        name='batch',
        field=models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT,
            related_name='%(class)s_sowings',
            to='plantings.productionbatch',
        ),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0020_backfill_production_batches'),
    ]

    operations = [require_batch(name) for name in SOWING_MODELS]
