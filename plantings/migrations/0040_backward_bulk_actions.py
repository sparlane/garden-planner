"""Offer the backward lifecycle facts through the reviewed bulk path."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('plantings', '0039_backward_lifecycle_events')]

    operations = [
        migrations.AlterField(
            model_name='bulkplantoperation',
            name='action',
            field=models.CharField(
                choices=[
                    ('germinate', 'Germinate'),
                    ('move', 'Move or transplant'),
                    ('stage', 'Update growth stage'),
                    ('grade', 'Update grade'),
                    ('repot', 'Pot on or repot'),
                    ('ready', 'Ready'),
                    ('retain', 'Retain'),
                    ('donate', 'Donate'),
                    ('fail', 'Fail'),
                    ('cull', 'Cull'),
                    ('finish_harvest', 'Finish harvest'),
                    ('hold_back', 'Hold back'),
                    ('end_retention', 'End retention'),
                ],
                max_length=24,
            ),
        ),
    ]
