import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0009_stocktake_approved_at_stocktake_approved_by_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='stocktaketarget',
            name='review_revision',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='stocktaketarget',
            name='review_snapshot',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='StocktakeReconciliation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phase', models.CharField(choices=[('post', 'Posted correction'), ('reverse', 'Reversal correction')], max_length=8)),
                ('domain', models.CharField(max_length=32)),
                ('result_app', models.CharField(max_length=64)),
                ('result_model', models.CharField(max_length=64)),
                ('result_object_id', models.PositiveBigIntegerField()),
                ('before', models.JSONField(default=dict)),
                ('after', models.JSONField(default=dict)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('reverses', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reversed_by', to='inventory.stocktakereconciliation')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reconciliations', to='inventory.stocktaketarget')),
            ],
            options={
                'ordering': ['created', 'pk'],
            },
        ),
    ]
