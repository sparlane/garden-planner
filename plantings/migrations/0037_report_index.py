"""Add the period access path used by production-loss reporting."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('plantings', '0036_commerce_lifecycle_events')]

    operations = [
        migrations.AddIndex(
            model_name='plantlifecycleevent',
            index=models.Index(
                fields=['workspace', 'occurred_at'], name='plant_event_report_idx',
            ),
        ),
    ]
