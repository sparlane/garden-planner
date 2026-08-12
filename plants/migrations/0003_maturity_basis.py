from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('plants', '0002_workspace_ownership'),
    ]

    operations = [
        migrations.AddField(
            model_name='plant',
            name='maturity_basis',
            field=models.CharField(
                choices=[('seed', 'From seed'), ('transplanting', 'From transplanting')],
                default='seed',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='plantvariety',
            name='maturity_basis',
            field=models.CharField(
                blank=True,
                choices=[('seed', 'From seed'), ('transplanting', 'From transplanting')],
                default=None,
                help_text='Leave blank to inherit the plant default.',
                max_length=16,
                null=True,
            ),
        ),
    ]
