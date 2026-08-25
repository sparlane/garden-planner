from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0013_evidenced_input_tax'),
    ]

    operations = [
        migrations.RemoveField(model_name='stockreceipt', name='price_includes_tax'),
        migrations.RemoveField(model_name='stockreceipt', name='tax_rate'),
        migrations.RemoveField(model_name='stockreceipt', name='tax_recoverable'),
    ]
