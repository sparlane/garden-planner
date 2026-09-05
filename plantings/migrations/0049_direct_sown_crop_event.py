from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import workspaces.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('garden', '0007_gardenbed_kind'),
        ('locations', '0004_backfill_location_paths'),
        ('plantings', '0048_sell_cohort_quantity'),
    ]

    operations = [
        migrations.CreateModel(
            name='DirectSownCropEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('emerged', 'Emerged'), ('thinned', 'Thinned'), ('failed_germination', 'Failed germination'), ('pest_loss', 'Pest loss'), ('removed', 'Removed'), ('retained', 'Retained count observed'), ('moved', 'Moved or transplanted'), ('individualized', 'Made into individual plants'), ('reversed', 'Reversed')], max_length=24)),
                ('occurred_on', models.DateField()),
                ('quantity', models.PositiveIntegerField(blank=True, null=True)),
                ('quantity_delta', models.IntegerField(default=0)),
                ('count_quality', models.CharField(blank=True, choices=[('exact', 'Exact'), ('estimated', 'Estimated'), ('unknown', 'Unknown')], default='', max_length=12)),
                ('notes', models.TextField(blank=True, default='')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('garden_square_after', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='garden.gardensquare')),
                ('garden_square_before', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='garden.gardensquare')),
                ('location_after', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='locations.location')),
                ('location_before', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='locations.location')),
                ('planting', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='direct_sown_events', to='plantings.gardenplanting')),
                ('reversal_of', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reversal', to='plantings.directsowncropevent')),
                ('workspace', models.ForeignKey(default=workspaces.models.get_default_workspace_id, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workspaces.workspace')),
            ],
            options={'ordering': ['occurred_on', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='directsowncropevent',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__isnull', True), ('quantity__gte', 1), _connector='OR'), name='direct_crop_event_quantity_positive'),
        ),
        migrations.AddConstraint(
            model_name='directsowncropevent',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('event_type', 'reversed'), ('reversal_of__isnull', False)), models.Q(models.Q(('event_type', 'reversed'), _negated=True), ('reversal_of__isnull', True)), _connector='OR'), name='direct_crop_reversal_has_target'),
        ),
    ]
