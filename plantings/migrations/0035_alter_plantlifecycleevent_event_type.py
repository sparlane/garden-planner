from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('plantings', '0034_alter_nurseryplaninputrequirement_requirement_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='plantlifecycleevent',
            name='event_type',
            field=models.CharField(choices=[('germinated', 'Germinated'), ('ready', 'Ready for sale or use'), ('transplanted', 'Transplanted or planted out'), ('retained', 'Retained'), ('failed', 'Failed'), ('lost', 'Lost during stocktake'), ('culled', 'Culled'), ('donated', 'Donated'), ('harvest_finished', 'Harvest finished'), ('corrected', 'Corrected')], max_length=20),
        ),
    ]
