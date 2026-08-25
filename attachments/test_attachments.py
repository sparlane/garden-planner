"""Private image processing, retention, and REST boundary tests."""

# Test names describe the contract and small Pillow fixtures are explicitly
# closed by each test's temporary-directory lifecycle.
# pylint: disable=missing-function-docstring,consider-using-with

import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from health.models import HealthObservation, HealthObservationType
from plantings.models import NurseryObservation
from tests.api import RESTContractTestCase
from tests.factories import make_harvest, make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from .models import ImageAttachment
from .processing import create_attachment
from .storage import private_attachment_storage


def image_upload(
    image_format='JPEG', *, size=(20, 10), name='garden photo.jpg',
    exif=None,
):
    """Build a small real image upload without fixture binaries."""
    output = BytesIO()
    mode = 'RGBA' if image_format == 'PNG' else 'RGB'
    image = Image.new(mode, size, (20, 100, 40, 128) if mode == 'RGBA' else (20, 100, 40))
    options = {} if exif is None else {'exif': exif}
    image.save(output, format=image_format, **options)
    return SimpleUploadedFile(name, output.getvalue(), content_type=f'image/{image_format.lower()}')


class AttachmentTestCase(RESTContractTestCase):
    """Use an isolated private directory for every attachment test."""

    def setUp(self):
        self.attachment_directory = TemporaryDirectory()
        self.settings_override = override_settings(
            ATTACHMENT_ROOT=Path(self.attachment_directory.name),
        )
        self.settings_override.enable()
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.plant = make_specific_plant(workspace=self.workspace)

    def tearDown(self):
        super().tearDown()
        self.settings_override.disable()
        self.attachment_directory.cleanup()

    def upload(self, upload=None, plant=None):
        """Post one multipart plant attachment."""
        return self.client.post('/attachments/', {
            'target_type': 'plant',
            'target_id': (plant or self.plant).pk,
            'image': upload or image_upload(),
        }, format='multipart')


class ImageProcessingTests(AttachmentTestCase):
    """Stored bytes are safe renderable images, not trusted uploads."""

    def test_orientation_metadata_and_thumbnail_are_normalized(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[36867] = '2026:08:25 09:30:00'
        exif[42033] = 'camera-serial-that-must-not-survive'

        response = self.upload(image_upload(exif=exif))

        self.assertEqual(response.status_code, 201, response.data)
        attachment = ImageAttachment.objects.get()
        self.assertEqual((attachment.width, attachment.height), (10, 20))
        self.assertEqual(
            attachment.captured_at.isoformat(), '2026-08-24T21:30:00+00:00',
        )
        with attachment.original.open('rb') as stored:
            normalized = Image.open(stored)
            self.assertEqual(normalized.getexif(), {})
            self.assertEqual(normalized.size, (10, 20))
        with attachment.thumbnail.open('rb') as stored:
            self.assertLessEqual(max(Image.open(stored).size), 512)
        self.assertEqual(attachment.byte_size, attachment.original.size)
        self.assertNotIn('garden photo', attachment.original.name)

    def test_supported_formats_are_sniffed_and_normalized(self):
        for image_format in ('JPEG', 'PNG', 'WEBP'):
            with self.subTest(image_format=image_format):
                response = self.upload(image_upload(image_format))
                self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ImageAttachment.objects.count(), 3)
        self.assertEqual(
            set(ImageAttachment.objects.values_list('content_type', flat=True)),
            {'image/jpeg', 'image/png'},
        )

    def test_invalid_animated_and_oversized_uploads_are_rejected(self):
        invalid = SimpleUploadedFile('not.jpg', b'not an image', content_type='image/jpeg')
        self.assertEqual(self.upload(invalid).status_code, 400)

        animated_bytes = BytesIO()
        frames = [Image.new('RGB', (4, 4), color) for color in ('red', 'blue')]
        frames[0].save(animated_bytes, format='GIF', save_all=True, append_images=frames[1:])
        animated = SimpleUploadedFile('moving.gif', animated_bytes.getvalue(), content_type='image/gif')
        self.assertEqual(self.upload(animated).status_code, 400)

        with override_settings(ATTACHMENT_MAX_BYTES=1):
            self.assertEqual(self.upload(image_upload()).status_code, 400)
        with override_settings(ATTACHMENT_MAX_PIXELS=10):
            self.assertEqual(self.upload(image_upload()).status_code, 400)
        self.assertFalse(ImageAttachment.objects.exists())


class AttachmentContractTests(AttachmentTestCase):
    """Files are immutable and visible only through the workspace API."""

    def test_upload_list_and_protected_content_contract(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        attachment_id = response.data['id']
        self.assertEqual(response.data['target_type'], 'plant')
        self.assertEqual(response.data['target_id'], self.plant.pk)
        self.assertNotIn(str(self.attachment_directory.name), str(response.data))

        listed = self.client.get('/attachments/', {
            'target_type': 'plant', 'target_id': self.plant.pk,
        })
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row['id'] for row in listed.data], [attachment_id])

        content = self.client.get(f'/attachments/{attachment_id}/content/')
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content['Cache-Control'], 'private, no-store')
        self.assertEqual(content['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(b''.join(content.streaming_content).startswith(b'\xff\xd8'))

        thumbnail = self.client.get(f'/attachments/{attachment_id}/thumbnail/')
        self.assertEqual(thumbnail.status_code, 200)
        self.assertTrue(b''.join(thumbnail.streaming_content))
        self.assertEqual(
            self.client.patch(f'/attachments/{attachment_id}/', {}).status_code,
            405,
        )
        self.assertEqual(
            self.client.delete(f'/attachments/{attachment_id}/').status_code,
            405,
        )

    def test_authentication_and_workspace_isolation_cover_every_lookup(self):
        own = self.upload().data['id']
        other_workspace = Workspace.objects.create(name='Other garden')
        other_plant = make_specific_plant(workspace=other_workspace)
        other = create_attachment(
            other_workspace, self.user, 'plant', other_plant, image_upload(),
        )

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get('/attachments/').status_code, 403)
        self.assertEqual(self.client.get(f'/attachments/{own}/content/').status_code, 403)
        self.client.force_authenticate(self.user)

        self.assertEqual(self.client.get(f'/attachments/{other.public_id}/').status_code, 404)
        self.assertEqual(
            self.client.get(f'/attachments/{other.public_id}/content/').status_code,
            404,
        )
        self.assertEqual(
            self.client.get('/attachments/', {
                'target_type': 'plant', 'target_id': other_plant.pk,
            }).data,
            [],
        )
        self.assertEqual(
            self.client.get(f'/attachments/originals/{other.public_id}.jpg').status_code,
            404,
        )

    def test_target_validation_and_immutability(self):
        other_workspace = Workspace.objects.create(name='Other garden')
        other_plant = make_specific_plant(workspace=other_workspace)
        response = self.upload(plant=other_plant)
        self.assertEqual(response.status_code, 400)

        attachment = create_attachment(
            self.workspace, self.user, 'plant', self.plant, image_upload(),
        )
        attachment.original_filename = 'changed.jpg'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            attachment.save()
        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            attachment.delete()

    def test_record_serializers_embed_protected_attachment_metadata(self):
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        nursery = NurseryObservation.objects.create(
            workspace=self.workspace, notes='Healthy new growth.',
        )
        health = HealthObservation.objects.create(
            workspace=self.workspace,
            observation_type=HealthObservationType.objects.get(
                workspace=self.workspace, code='pest-signs',
            ),
            severity=HealthObservation.Severity.LOW,
        )
        harvest = make_harvest(workspace=self.workspace)
        targets = (
            ('plant', self.plant, f'/plantings/specificplants/{self.plant.pk}/'),
            ('nursery_observation', nursery, '/plantings/nursery-observations/'),
            ('health_observation', health, '/health/observations/'),
            ('harvest', harvest, f'/plantings/harvests/{harvest.pk}/'),
        )
        for target_type, target, url in targets:
            create_attachment(
                self.workspace, self.user, target_type, target, image_upload(),
            )
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.data)
            payload = response.data
            if isinstance(payload, list):
                payload = next(row for row in payload if row['pk'] == target.pk)
            with self.subTest(target_type=target_type):
                self.assertEqual(payload['attachments'][0]['target_type'], target_type)


class AttachmentArchiveTests(AttachmentTestCase):
    """Photo archives are scoped, self-validating, and idempotent."""

    def archive_bytes(self):
        response = self.client.get('/attachments/archive/')
        self.assertEqual(response.status_code, 200)
        return b''.join(response.streaming_content)

    def restore(self, data, *, dry_run):
        return self.client.post('/attachments/archive/restore/', {
            'archive': SimpleUploadedFile(
                'photos.zip', data, content_type='application/zip',
            ),
            'dry_run': str(dry_run).lower(),
        }, format='multipart')

    def rewrite_manifest(self, data, change):
        source = ZipFile(BytesIO(data))
        output = BytesIO()
        with source, ZipFile(output, 'w', compression=ZIP_DEFLATED) as target:
            manifest = json.loads(source.read('manifest.json'))
            change(manifest)
            for info in source.infolist():
                content = (
                    json.dumps(manifest).encode() if info.filename == 'manifest.json'
                    else source.read(info.filename)
                )
                target.writestr(info.filename, content)
        return output.getvalue()

    def test_export_contains_only_current_workspace_originals(self):
        attachment = create_attachment(
            self.workspace, self.user, 'plant', self.plant, image_upload(),
        )
        other_workspace = Workspace.objects.create(name='Other garden')
        other_plant = make_specific_plant(workspace=other_workspace)
        create_attachment(
            other_workspace, self.user, 'plant', other_plant, image_upload(),
        )

        with ZipFile(BytesIO(self.archive_bytes())) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['format'], 'garden-tracker-photo-archive')
            self.assertEqual(manifest['version'], 1)
            self.assertEqual(
                [row['id'] for row in manifest['attachments']],
                [str(attachment.public_id)],
            )
            self.assertEqual(
                set(archive.namelist()),
                {'manifest.json', manifest['attachments'][0]['path']},
            )
            self.assertFalse(any('thumbnail' in name for name in archive.namelist()))

    def test_dry_run_then_apply_round_trips_and_is_idempotent(self):
        attachment = create_attachment(
            self.workspace, self.user, 'plant', self.plant, image_upload(),
        )
        attachment_id = attachment.public_id
        original_names = [attachment.original.name, attachment.thumbnail.name]
        archive = self.archive_bytes()
        ImageAttachment.objects.filter(pk=attachment.pk)._raw_delete(  # pylint: disable=protected-access
            ImageAttachment.objects.db,
        )
        for name in original_names:
            private_attachment_storage.delete(name)

        preview = self.restore(archive, dry_run=True)
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['would_create'], 1)
        self.assertFalse(ImageAttachment.objects.exists())

        applied = self.restore(archive, dry_run=False)
        self.assertEqual(applied.status_code, 200, applied.data)
        self.assertEqual(applied.data['created'], 1)
        restored = ImageAttachment.objects.get(public_id=attachment_id)
        self.assertEqual(restored.plant, self.plant)
        self.assertTrue(restored.original.storage.exists(restored.original.name))

        repeated = self.restore(archive, dry_run=False)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['already_present'], 1)
        self.assertEqual(repeated.data['created'], 0)

    def test_restore_rejects_missing_targets_and_checksum_changes(self):
        create_attachment(
            self.workspace, self.user, 'plant', self.plant, image_upload(),
        )
        archive = self.archive_bytes()
        missing = self.rewrite_manifest(
            archive,
            lambda manifest: manifest['attachments'][0].update(target_id=999999),
        )
        preview = self.restore(missing, dry_run=True)
        self.assertFalse(preview.data['valid'])
        self.assertTrue(any('target' in error for error in preview.data['errors']))

        checksum = self.rewrite_manifest(
            archive,
            lambda manifest: manifest['attachments'][0].update(sha256='0' * 64),
        )
        rejected = self.restore(checksum, dry_run=False)
        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(any('checksum' in error for error in rejected.data['errors']))

    def test_archive_routes_require_authentication_and_enforce_size_limit(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get('/attachments/archive/').status_code, 403)
        self.assertEqual(
            self.restore(b'not a zip', dry_run=True).status_code,
            403,
        )
        self.client.force_authenticate(self.user)
        with override_settings(ATTACHMENT_ARCHIVE_MAX_BYTES=1):
            response = self.restore(b'larger than one byte', dry_run=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['valid'])
        self.assertIn('too large', response.data['errors'][0])
