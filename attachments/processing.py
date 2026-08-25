"""Validate and sanitize uploaded images before private storage."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ImageAttachment
from .storage import private_attachment_storage


ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
CAPTURE_TIME_TAG = 36867
CAPTURE_OFFSET_TAG = 36881


@dataclass(frozen=True)
class ProcessedImage:
    """Sanitized image bytes and the metadata retained in the database."""

    original: bytes
    thumbnail: bytes
    content_type: str
    width: int
    height: int
    captured_at: datetime | None


def _capture_time(image, workspace):
    exif = image.getexif()
    value = exif.get(CAPTURE_TIME_TAG)
    if not value:
        return None
    try:
        captured = datetime.strptime(str(value), '%Y:%m:%d %H:%M:%S')
        offset = exif.get(CAPTURE_OFFSET_TAG)
        if offset:
            return datetime.fromisoformat(f'{captured.isoformat()}{offset}')
        return captured.replace(tzinfo=ZoneInfo(workspace.timezone))
    except (TypeError, ValueError):
        return None


def _output_kind(image):
    has_alpha = image.mode in {'RGBA', 'LA'} or (
        image.mode == 'P' and 'transparency' in image.info
    )
    return ('PNG', 'image/png') if has_alpha else ('JPEG', 'image/jpeg')


def _encode(image, output_format, *, thumbnail=False):
    output = BytesIO()
    prepared = image
    if output_format == 'JPEG' and image.mode != 'RGB':
        prepared = image.convert('RGB')
    if output_format == 'JPEG':
        prepared.save(output, format='JPEG', quality=88 if thumbnail else 92, optimize=True)
    else:
        prepared.save(output, format='PNG', optimize=True)
    return output.getvalue()


def process_image(upload, workspace):
    """Return safe, oriented image bytes or a field-friendly validation error."""
    if upload.size > settings.ATTACHMENT_MAX_BYTES:
        raise ValidationError({'image': 'The image must be 15 MiB or smaller.'})
    try:
        upload.seek(0)
        image = Image.open(upload)
        image.load()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValidationError({'image': 'Upload a valid JPEG, PNG, or WebP image.'}) from exc
    if image.format not in ALLOWED_FORMATS:
        raise ValidationError({'image': 'Upload a JPEG, PNG, or WebP image.'})
    if getattr(image, 'n_frames', 1) != 1:
        raise ValidationError({'image': 'Animated images are not supported.'})
    if image.width * image.height > settings.ATTACHMENT_MAX_PIXELS:
        raise ValidationError({'image': 'The image must contain 40 megapixels or fewer.'})
    captured_at = _capture_time(image, workspace)
    image = ImageOps.exif_transpose(image)
    output_format, content_type = _output_kind(image)
    original = _encode(image, output_format)
    thumbnail = image.copy()
    thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
    return ProcessedImage(
        original=original,
        thumbnail=_encode(thumbnail, output_format, thumbnail=True),
        content_type=content_type,
        width=image.width,
        height=image.height,
        captured_at=captured_at,
    )


def _safe_filename(name):
    return Path(name or 'photo').name[:255]


def create_attachment(workspace, user, target_type, target, upload):
    """Sanitize and atomically describe one immutable attachment."""
    processed = process_image(upload, workspace)
    field = ImageAttachment.TARGET_FIELDS[target_type]
    attachment = ImageAttachment(
        workspace=workspace,
        uploaded_by=user if user and user.is_authenticated else None,
        original_filename=_safe_filename(upload.name),
        content_type=processed.content_type,
        byte_size=len(processed.original),
        width=processed.width,
        height=processed.height,
        sha256=sha256(processed.original).hexdigest(),
        captured_at=processed.captured_at,
        **{field: target},
    )
    extension = 'png' if processed.content_type == 'image/png' else 'jpg'
    attachment.original = ContentFile(processed.original, name=f'photo.{extension}')
    attachment.thumbnail = ContentFile(processed.thumbnail, name=f'thumbnail.{extension}')
    try:
        attachment.save()
    except Exception:
        for stored in (attachment.original, attachment.thumbnail):
            if stored.name and private_attachment_storage.exists(stored.name):
                private_attachment_storage.delete(stored.name)
        raise
    return attachment
