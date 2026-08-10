import secrets

from django.db import migrations


ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
TARGETS = (
    ('plantings', 'SpecificPlant', 'PLT'),
    ('seedtrays', 'SeedTray', 'TRY'),
    ('plantings', 'ProductionBatch', 'BAT'),
    ('locations', 'Location', 'LOC'),
    ('garden', 'GardenArea', 'GAR'),
)


def checksum(value):
    total = sum((index + 1) * ord(character) for index, character in enumerate(value))
    return ALPHABET[total % len(ALPHABET)]


def new_code(prefix):
    body = ''.join(secrets.choice(ALPHABET) for _ in range(12))
    stem = f'{prefix}-{body}'
    return f'{stem}-{checksum(stem)}'


def backfill(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Identity = apps.get_model('labels', 'LabelIdentity')
    Code = apps.get_model('labels', 'LabelCode')
    Template = apps.get_model('labels', 'LabelTemplate')
    Workspace = apps.get_model('workspaces', 'Workspace')

    for app_label, model_name, prefix in TARGETS:
        Model = apps.get_model(app_label, model_name)
        content_type, _created = ContentType.objects.get_or_create(
            app_label=app_label,
            model=model_name.lower(),
        )
        for target in Model.objects.all().iterator():
            identity = Identity.objects.create(
                workspace_id=target.workspace_id,
                target_content_type=content_type,
                target_object_id=target.pk,
                target_snapshot={'display': str(target), 'pk': target.pk},
            )
            Code.objects.create(
                workspace_id=target.workspace_id,
                identity=identity,
                code=new_code(prefix),
            )

    presets = (
        ('Single QR 100 × 50 mm', 'qr', 'url', 'single', {'label_width_mm': 100, 'label_height_mm': 50}),
        ('A4 sheet QR 63.5 × 38.1 mm', 'qr', 'url', 'sheet', {'label_width_mm': 63.5, 'label_height_mm': 38.1, 'page_width_mm': 210, 'page_height_mm': 297, 'margin_mm': 7, 'gap_mm': 2}),
        ('Roll Code 128 50 × 30 mm', 'code128', 'code', 'roll', {'label_width_mm': 50, 'label_height_mm': 30}),
    )
    for workspace in Workspace.objects.all():
        for name, format_name, payload_mode, layout, dimensions in presets:
            Template.objects.create(
                workspace=workspace,
                name=name,
                format=format_name,
                payload_mode=payload_mode,
                layout=layout,
                fields=['display', 'variety', 'batch', 'sowing_date', 'expected_ready', 'code', 'print_date'],
                dimensions=dimensions,
                built_in=True,
            )


class Migration(migrations.Migration):
    dependencies = [
        ('labels', '0001_initial'),
        ('plantings', '0027_bulkplantoperation_bulkplantoperationresult_and_more'),
        ('seedtrays', '0006_backfill_legacy_generations'),
        ('locations', '0003_hierarchy_and_capacity'),
        ('garden', '0006_gardengeometryconfirmation'),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
