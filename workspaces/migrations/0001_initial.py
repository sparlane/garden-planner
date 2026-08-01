# Generated manually for the workspace ownership boundary.

import decimal

import django.core.validators
from django.db import migrations, models

import workspaces.models


def create_default_workspace(apps, _schema_editor):
    """Create the compatibility workspace used by existing installations."""
    workspace_model = apps.get_model('workspaces', 'Workspace')
    workspace_model.objects.create(
        pk=1,
        name='My Garden',
        mode='garden',
        currency_code='USD',
        default_tax_rate=decimal.Decimal('0'),
        timezone='UTC',
        measurement_system='metric',
    )


def remove_default_workspace(apps, _schema_editor):
    """Remove only the compatibility workspace on migration reversal."""
    workspace_model = apps.get_model('workspaces', 'Workspace')
    workspace_model.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Workspace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('mode', models.CharField(choices=[('garden', 'Garden'), ('nursery', 'Nursery')], default='garden', max_length=16)),
                ('currency_code', models.CharField(default='USD', max_length=3, validators=[django.core.validators.RegexValidator(message='Enter a three-letter uppercase ISO 4217 currency code.', regex='^[A-Z]{3}$')])),
                ('default_tax_rate', models.DecimalField(decimal_places=4, default=decimal.Decimal('0'), help_text='Default tax percentage from 0 through 100.', max_digits=7, validators=[django.core.validators.MinValueValidator(decimal.Decimal('0')), django.core.validators.MaxValueValidator(decimal.Decimal('100'))])),
                ('timezone', models.CharField(default='UTC', max_length=64, validators=[workspaces.models.validate_iana_timezone])),
                ('measurement_system', models.CharField(choices=[('metric', 'Metric'), ('imperial', 'Imperial')], default='metric', max_length=16)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RunPython(create_default_workspace, remove_default_workspace),
    ]
