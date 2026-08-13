import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("seeds", "0007_point_packets_at_the_locations_app"),
    ]

    operations = [
        migrations.AddField(
            model_name="seedpacketquantityreconciliation",
            name="reversal_of",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reversal",
                to="seeds.seedpacketquantityreconciliation",
            ),
        ),
    ]
