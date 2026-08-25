from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('supplies', '0003_supplier_is_system_default_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='address',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='supplier',
            name='gst_number',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.AddField(
            model_name='supplier',
            name='gst_status',
            field=models.CharField(
                choices=[
                    ('registered', 'GST registered'),
                    ('unregistered', 'Not GST registered'),
                    ('unknown', 'Unknown'),
                ],
                default='unknown',
                max_length=16,
            ),
        ),
    ]
