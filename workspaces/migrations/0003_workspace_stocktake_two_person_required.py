from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0002_workspace_override_tolerance_floor_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='stocktake_two_person_required',
            field=models.BooleanField(default=False, help_text='Require a stocktake reviewer to be different from every counter.'),
        ),
    ]
