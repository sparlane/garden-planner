# Generated by Django 5.0.8 on 2024-09-07 04:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plantings", "0003_gardensquaretransplant"),
    ]

    operations = [
        migrations.AddField(
            model_name="gardenrowdirectsowplanting",
            name="removed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gardensquaredirectsowplanting",
            name="removed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gardensquaretransplant",
            name="removed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="seedtrayplanting",
            name="removed",
            field=models.BooleanField(default=False),
        ),
    ]
