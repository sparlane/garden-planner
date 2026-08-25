"""Versioned workspace photo export and validated restore."""

import json
import shutil
import stat
from hashlib import sha256
from io import BytesIO
from tempfile import SpooledTemporaryFile
from uuid import UUID
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.dateparse import parse_datetime
from PIL import Image, UnidentifiedImageError

from .models import ImageAttachment
from .processing import _encode  # pylint: disable=protected-access
from .storage import private_attachment_storage


ARCHIVE_FORMAT = 'garden-tracker-photo-archive'
ARCHIVE_VERSION = 1
MANIFEST_PATH = 'manifest.json'
FORMAT_CONTENT_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png'}


def _manifest_row(attachment):
    extension = 'png' if attachment.content_type == 'image/png' else 'jpg'
    return {
        'id': str(attachment.public_id),
        'target_type': attachment.target_type,
        'target_id': attachment.target_id,
        'original_filename': attachment.original_filename,
        'content_type': attachment.content_type,
        'byte_size': attachment.byte_size,
        'width': attachment.width,
        'height': attachment.height,
        'sha256': attachment.sha256,
        'captured_at': (
            attachment.captured_at.isoformat() if attachment.captured_at else None
        ),
        'created': attachment.created.isoformat(),
        'path': f'photos/{attachment.public_id}.{extension}',
    }


def export_archive(workspace):
    """Build a seeked ZIP containing this workspace's sanitized originals."""
    attachments = list(
        ImageAttachment.objects.filter(workspace=workspace).order_by('created', 'pk')
    )
    rows = [_manifest_row(attachment) for attachment in attachments]
    # FileResponse owns and closes this returned stream after sending it.
    output = SpooledTemporaryFile(  # pylint: disable=consider-using-with
        max_size=16 * 1024 * 1024, mode='w+b',
    )
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST_PATH, json.dumps({
            'format': ARCHIVE_FORMAT,
            'version': ARCHIVE_VERSION,
            'workspace': {'id': workspace.pk, 'name': workspace.name},
            'attachments': rows,
        }, indent=2).encode('utf-8'))
        for attachment, row in zip(attachments, rows, strict=True):
            with attachment.original.open('rb') as source, archive.open(row['path'], 'w') as target:
                shutil.copyfileobj(source, target)
    output.seek(0)
    return output


def _parse_timestamp(value, field, errors, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        errors.append(f'{field} must be an ISO-8601 timestamp.')
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        errors.append(f'{field} must include a timezone.')
        return None
    return parsed


def _inspect_image(data, row, errors):
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        errors.append(f'{row.get("path", "photo")}: invalid image ({exc}).')
        return None
    content_type = FORMAT_CONTENT_TYPES.get(image.format)
    if content_type is None or content_type != row.get('content_type'):
        errors.append(f'{row.get("path", "photo")}: image format does not match its manifest.')
    if getattr(image, 'n_frames', 1) != 1:
        errors.append(f'{row.get("path", "photo")}: animated images are not supported.')
    if image.width * image.height > settings.ATTACHMENT_MAX_PIXELS:
        errors.append(f'{row.get("path", "photo")}: image exceeds the pixel limit.')
    if image.getexif():
        errors.append(f'{row.get("path", "photo")}: image still contains EXIF metadata.')
    if [image.width, image.height] != [row.get('width'), row.get('height')]:
        errors.append(f'{row.get("path", "photo")}: dimensions do not match its manifest.')
    thumbnail = image.copy()
    thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
    output_format = 'PNG' if content_type == 'image/png' else 'JPEG'
    return _encode(thumbnail, output_format, thumbnail=True)


def _safe_zip(archive, errors):
    infos = archive.infolist()
    if len(infos) > settings.ATTACHMENT_ARCHIVE_MAX_ENTRIES + 1:
        errors.append('The archive contains too many entries.')
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        errors.append('The archive contains duplicate paths.')
    if sum(info.file_size for info in infos) > settings.ATTACHMENT_ARCHIVE_MAX_EXPANDED_BYTES:
        errors.append('The expanded archive is too large.')
    for info in infos:
        if info.filename.startswith('/') or '..' in info.filename.split('/'):
            errors.append(f'Unsafe archive path: {info.filename}.')
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            errors.append(f'Symbolic links are not permitted: {info.filename}.')
    return set(names)


def _target(workspace, target_type, target_id, target_mapping):
    try:
        mapped_type, mapped_id = target_mapping(target_type, target_id)
    except (TypeError, ValueError):
        return None, None, None, None
    field = ImageAttachment.TARGET_FIELDS.get(mapped_type)
    if field is None:
        return None, None, None, None
    model = ImageAttachment._meta.get_field(field).remote_field.model
    try:
        target = model.objects.filter(workspace=workspace, pk=mapped_id).first()
    except (TypeError, ValueError):
        target = None
    return field, target, mapped_type, mapped_id


def _inspect_archive(workspace, upload, target_mapping):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    errors = []
    prepared = []
    already_present = 0
    if upload.size > settings.ATTACHMENT_ARCHIVE_MAX_BYTES:
        return {'valid': False, 'would_create': 0, 'already_present': 0, 'errors': ['The archive is too large.']}, []
    try:
        upload.seek(0)
        archive = ZipFile(upload)
    except (BadZipFile, OSError):
        return {'valid': False, 'would_create': 0, 'already_present': 0, 'errors': ['Upload a valid photo archive ZIP.']}, []
    with archive:
        names = _safe_zip(archive, errors)
        if MANIFEST_PATH not in names:
            errors.append('The archive has no manifest.json.')
            return {'valid': False, 'would_create': 0, 'already_present': 0, 'errors': errors}, []
        try:
            manifest = json.loads(archive.read(MANIFEST_PATH))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
            errors.append('The archive manifest is not valid JSON.')
            return {'valid': False, 'would_create': 0, 'already_present': 0, 'errors': errors}, []
        if manifest.get('format') != ARCHIVE_FORMAT or manifest.get('version') != ARCHIVE_VERSION:
            errors.append('The archive format or version is not supported.')
        rows = manifest.get('attachments')
        if not isinstance(rows, list):
            errors.append('The manifest attachments value must be a list.')
            rows = []
        expected_paths = {MANIFEST_PATH}
        seen_ids = set()
        seen_paths = set()
        for index, row in enumerate(rows):
            label = f'attachments[{index}]'
            if not isinstance(row, dict):
                errors.append(f'{label} must be an object.')
                continue
            path = row.get('path')
            if not isinstance(path, str) or not path.startswith('photos/'):
                errors.append(f'{label}.path is invalid.')
                continue
            expected_paths.add(path)
            if path in seen_paths:
                errors.append(f'{label}.path is duplicated in the manifest.')
                continue
            seen_paths.add(path)
            try:
                public_id = UUID(str(row.get('id')))
            except ValueError:
                errors.append(f'{label}.id is not a UUID.')
                continue
            if public_id in seen_ids:
                errors.append(f'{label}.id is duplicated.')
                continue
            seen_ids.add(public_id)
            if path not in names:
                errors.append(f'{path} is missing from the archive.')
                continue
            data = archive.read(path)
            digest = sha256(data).hexdigest()
            if digest != row.get('sha256') or len(data) != row.get('byte_size'):
                errors.append(f'{path}: checksum or byte size does not match its manifest.')
            thumbnail = _inspect_image(data, row, errors)
            captured_at = _parse_timestamp(
                row.get('captured_at'), f'{label}.captured_at', errors, nullable=True,
            )
            created = _parse_timestamp(row.get('created'), f'{label}.created', errors)
            if not isinstance(row.get('original_filename'), str):
                errors.append(f'{label}.original_filename must be text.')
            field, target, mapped_type, mapped_id = _target(
                workspace, row.get('target_type'), row.get('target_id'), target_mapping,
            )
            if field is None or target is None:
                errors.append(f'{label} does not identify a target in this workspace.')
            existing = ImageAttachment.objects.filter(public_id=public_id).first()
            if existing is not None:
                if all((
                    existing.workspace_id == workspace.pk,
                    existing.sha256 == digest,
                    existing.target_type == mapped_type,
                    existing.target_id == mapped_id,
                )):
                    already_present += 1
                    continue
                errors.append(f'{label}.id conflicts with an existing attachment.')
            if thumbnail is not None and target is not None and created is not None:
                prepared.append({
                    'row': row, 'public_id': public_id, 'data': data,
                    'thumbnail': thumbnail, 'captured_at': captured_at,
                    'created': created, 'field': field, 'target': target,
                })
        extras = names - expected_paths
        if extras:
            errors.append(f'Unexpected archive entries: {sorted(extras)}.')
    report = {
        'valid': not errors,
        'would_create': len(prepared) if not errors else 0,
        'already_present': already_present,
        'errors': errors,
    }
    return report, prepared


def restore_archive(workspace, user, upload, *, dry_run=True, target_mapping=None):
    """Validate, then optionally restore, one photo-only workspace archive."""
    mapper = target_mapping or (lambda target_type, target_id: (target_type, target_id))
    report, prepared = _inspect_archive(workspace, upload, mapper)
    if dry_run or not report['valid']:
        return report
    stored_names = []
    try:
        with transaction.atomic():
            for item in prepared:
                row = item['row']
                extension = 'png' if row['content_type'] == 'image/png' else 'jpg'
                attachment = ImageAttachment(
                    workspace=workspace, public_id=item['public_id'],
                    uploaded_by=user if user and user.is_authenticated else None,
                    original_filename=row['original_filename'][:255],
                    content_type=row['content_type'], byte_size=row['byte_size'],
                    width=row['width'], height=row['height'], sha256=row['sha256'],
                    captured_at=item['captured_at'], **{item['field']: item['target']},
                )
                attachment.original = ContentFile(item['data'], name=f'photo.{extension}')
                attachment.thumbnail = ContentFile(item['thumbnail'], name=f'thumbnail.{extension}')
                attachment.save()
                stored_names.extend([attachment.original.name, attachment.thumbnail.name])
                ImageAttachment.objects.filter(pk=attachment.pk).update(created=item['created'])
    except Exception:
        for name in stored_names:
            if private_attachment_storage.exists(name):
                private_attachment_storage.delete(name)
        raise
    return {**report, 'created': len(prepared)}
