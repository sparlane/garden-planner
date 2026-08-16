"""Record holding stock back and ending a retention as facts of their own."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('plantings', '0038_released_available_event')]

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
                    ('released_available', 'Released from quarantine'),
                    ('held_back', 'Held back from sale'),
                    ('retention_ended', 'Retention ended'),
                    ('corrected', 'Corrected'),
                ],
                max_length=24,
            ),
        ),
    ]
