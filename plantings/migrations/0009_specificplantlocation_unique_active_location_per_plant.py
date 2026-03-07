from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plantings", "0008_specificplantlocation"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="specificplantlocation",
            constraint=models.UniqueConstraint(
                condition=models.Q(ended__isnull=True),
                fields=["specific_plant"],
                name="unique_active_location_per_plant",
            ),
        ),
    ]
