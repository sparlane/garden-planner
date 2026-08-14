from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('plantings', '0035_alter_plantlifecycleevent_event_type')]

    operations = [
        migrations.AlterField(
            model_name='plantlifecycleevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('germinated', 'Germinated'),
                    ('ready', 'Ready for sale or use'),
                    ('transplanted', 'Transplanted or planted out'),
                    ('retained', 'Retained'),
                    ('failed', 'Failed'),
                    ('lost', 'Lost during stocktake'),
                    ('culled', 'Culled'),
                    ('donated', 'Donated'),
                    ('harvest_finished', 'Harvest finished'),
                    ('sold', 'Sold'),
                    ('returned_available', 'Returned available'),
                    ('returned_quarantined', 'Returned quarantined'),
                    ('returned_discarded', 'Returned discarded'),
                    ('corrected', 'Corrected'),
                ],
                max_length=24,
            ),
        ),
    ]
