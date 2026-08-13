import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0008_inventoryitem_container_footprint_m2_and_more'),
        ('locations', '0004_backfill_location_paths'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='stocktake',
            name='approved_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='approved_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='blind',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='posted_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='reversed_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='reviewed_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='scope',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='stocktake',
            name='scope_digest',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AlterField(
            model_name='stocktake',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('open', 'Open'), ('paused', 'Paused'), ('review', 'In review'), ('approved', 'Approved'), ('posted', 'Posted'), ('reversed', 'Reversed')], default='draft', editable=False, max_length=16),
        ),
        migrations.CreateModel(
            name='StocktakeCount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('counted_quantity', models.DecimalField(blank=True, decimal_places=9, max_digits=24, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('observed_state', models.CharField(blank=True, default='', max_length=32)),
                ('code_snapshot', models.CharField(blank=True, default='', max_length=64)),
                ('resolved_identity', models.JSONField(blank=True, default=dict)),
                ('notes', models.TextField(blank=True, default='')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('counter', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('observed_location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='locations.location')),
            ],
            options={
                'ordering': ['created', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='StocktakeTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(choices=[('lot', 'Consumable lot'), ('seed_packet', 'Seed packet'), ('tray', 'Serialized tray'), ('cohort', 'Plant cohort'), ('plant', 'Individual plant')], max_length=16)),
                ('target_key', models.CharField(max_length=96)),
                ('target_object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('display', models.CharField(max_length=255)),
                ('expected_quantity', models.DecimalField(blank=True, decimal_places=9, max_digits=24, null=True)),
                ('expected_state', models.CharField(blank=True, default='', max_length=32)),
                ('expected_snapshot', models.JSONField(default=dict)),
                ('source_revision', models.CharField(max_length=64)),
                ('unexpected', models.BooleanField(default=False)),
                ('count_status', models.CharField(choices=[('pending', 'Pending'), ('counted', 'Counted'), ('recount', 'Recount requested')], default='pending', max_length=16)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('accepted_count', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='accepted_for', to='inventory.stocktakecount')),
                ('expected_location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='locations.location')),
                ('stocktake', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='targets', to='inventory.stocktake')),
            ],
            options={
                'ordering': ['target_type', 'display', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='stocktakecount',
            name='target',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='counts', to='inventory.stocktaketarget'),
        ),
        migrations.CreateModel(
            name='StocktakeAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=2048)),
                ('label', models.CharField(blank=True, default='', max_length=255)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('stocktake', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attachments', to='inventory.stocktake')),
                ('target', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='attachments', to='inventory.stocktaketarget')),
            ],
            options={
                'ordering': ['created', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='StocktakeVariance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('quantity', 'Quantity'), ('missing', 'Missing'), ('excess', 'Excess'), ('misplaced', 'Misplaced'), ('state_mismatch', 'State mismatch')], max_length=20)),
                ('expected', models.JSONField(default=dict)),
                ('observed', models.JSONField(default=dict)),
                ('source_changed', models.BooleanField(default=False)),
                ('current_revision', models.CharField(blank=True, default='', max_length=64)),
                ('conflict_resolution', models.CharField(blank=True, choices=[('', 'No conflict'), ('accepted', 'Accepted current conflict'), ('refreshed', 'Refreshed snapshot'), ('recount', 'Recount requested')], default='', max_length=12)),
                ('conflict_reason', models.TextField(blank=True, default='')),
                ('resolution_action', models.CharField(blank=True, default='', max_length=32)),
                ('resolution_payload', models.JSONField(blank=True, default=dict)),
                ('resolution_reason', models.TextField(blank=True, default='')),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='variances', to='inventory.stocktaketarget')),
            ],
            options={
                'ordering': ['target_id', 'kind', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='stocktaketarget',
            constraint=models.UniqueConstraint(fields=('stocktake', 'target_key'), name='inventory_stocktake_target_key_unique'),
        ),
        migrations.AddConstraint(
            model_name='stocktakeattachment',
            constraint=models.UniqueConstraint(fields=('stocktake', 'url'), name='inventory_stocktake_attachment_url_unique'),
        ),
        migrations.AddConstraint(
            model_name='stocktakevariance',
            constraint=models.UniqueConstraint(fields=('target', 'kind'), name='inventory_stocktake_variance_kind_unique'),
        ),
    ]
