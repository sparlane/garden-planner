# Generated by Django 5.0.6 on 2024-07-06 01:32

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("plants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Supplier",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=1024)),
                ("website", models.CharField(blank=True, max_length=1024, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="Seeds",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "supplier_code",
                    models.CharField(blank=True, max_length=32, null=True),
                ),
                ("url", models.CharField(blank=True, max_length=1024, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "plant_variety",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="plants.plantvariety",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="seeds.supplier"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SeedPacket",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("purchase_date", models.DateField(blank=True, null=True)),
                ("sow_by", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "seeds",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="seeds.seeds"
                    ),
                ),
            ],
        ),
    ]
