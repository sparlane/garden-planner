from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_normalize_cell_volume_usage'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockreceipt',
            name='price_includes_tax',
            field=models.BooleanField(
                default=False,
                help_text='Whether entered receipt prices include the receipt tax rate.',
            ),
        ),
    ]
