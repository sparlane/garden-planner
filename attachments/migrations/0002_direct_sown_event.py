from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('attachments', '0001_initial'),
        ('plantings', '0049_direct_sown_crop_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='imageattachment',
            name='direct_sown_event',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='image_attachments', to='plantings.directsowncropevent'),
        ),
        migrations.RemoveConstraint(
            model_name='imageattachment',
            name='attachment_exactly_one_target',
        ),
        migrations.AddConstraint(
            model_name='imageattachment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('direct_sown_event__isnull', True), ('harvest__isnull', True), ('health_observation__isnull', True), ('nursery_observation__isnull', True), ('plant__isnull', False)), models.Q(('direct_sown_event__isnull', True), ('harvest__isnull', True), ('health_observation__isnull', True), ('nursery_observation__isnull', False), ('plant__isnull', True)), models.Q(('direct_sown_event__isnull', True), ('harvest__isnull', True), ('health_observation__isnull', False), ('nursery_observation__isnull', True), ('plant__isnull', True)), models.Q(('direct_sown_event__isnull', True), ('harvest__isnull', False), ('health_observation__isnull', True), ('nursery_observation__isnull', True), ('plant__isnull', True)), models.Q(('direct_sown_event__isnull', False), ('harvest__isnull', True), ('health_observation__isnull', True), ('nursery_observation__isnull', True), ('plant__isnull', True)), _connector='OR'), name='attachment_exactly_one_target'),
        ),
        migrations.AddIndex(
            model_name='imageattachment',
            index=models.Index(fields=['workspace', 'direct_sown_event'], name='attachments_workspa_531450_idx'),
        ),
    ]
