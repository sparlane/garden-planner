"""Issuance and lifecycle services for physical label codes."""

import secrets

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import LabelCode, LabelIdentity


ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
TARGET_PREFIXES = {
    ('plantings', 'specificplant'): 'PLT',
    ('seedtrays', 'seedtray'): 'TRY',
    ('plantings', 'productionbatch'): 'BAT',
    ('locations', 'location'): 'LOC',
    ('garden', 'gardenarea'): 'GAR',
}


def normalize_code(value):
    """Normalize scanner and keyboard input without accepting embedded spaces."""
    return str(value).strip().upper()


def code_checksum(value):
    """Return one check character for the normalized prefix and random body."""
    total = sum((index + 1) * ord(character) for index, character in enumerate(value))
    return ALPHABET[total % len(ALPHABET)]


def valid_code(value):
    """Return whether a code has a supported prefix and a valid check character."""
    code = normalize_code(value)
    parts = code.split('-')
    if len(parts) != 3 or parts[0] not in TARGET_PREFIXES.values():
        return False
    body_valid = len(parts[1]) == 12 and all(
        character in ALPHABET for character in parts[1]
    )
    return body_valid and parts[2] == code_checksum(f'{parts[0]}-{parts[1]}')


def target_key(target):
    """Return the supported content-type key for a concrete model instance."""
    content_type = ContentType.objects.get_for_model(target, for_concrete_model=True)
    key = (content_type.app_label, content_type.model)
    if key not in TARGET_PREFIXES:
        raise ValidationError({'target': 'This object type cannot receive a label.'})
    return content_type, key


def target_snapshot(target):
    """Retain a small useful name even if the source object is later deleted."""
    return {'display': str(target), 'pk': target.pk}


def _new_code(prefix):
    body = ''.join(secrets.choice(ALPHABET) for _ in range(12))
    stem = f'{prefix}-{body}'
    return f'{stem}-{code_checksum(stem)}'


@transaction.atomic
def ensure_identity(target, user=None):
    """Return the target's identity, issuing its first code exactly once."""
    content_type, key = target_key(target)
    identity, _created = LabelIdentity.objects.get_or_create(
        workspace=target.workspace,
        target_content_type=content_type,
        target_object_id=target.pk,
        defaults={'target_snapshot': target_snapshot(target)},
    )
    if identity.codes.filter(status=LabelCode.Status.ACTIVE).exists():
        return identity
    for _attempt in range(8):
        try:
            with transaction.atomic():
                LabelCode.objects.create(
                    workspace=target.workspace,
                    identity=identity,
                    code=_new_code(TARGET_PREFIXES[key]),
                    issued_by=user,
                )
            return identity
        except IntegrityError:
            continue
    raise RuntimeError('Unable to issue a unique label code.')


@transaction.atomic
def replace_code(code, user, reason):
    """Retire one active code and atomically issue its successor."""
    if not reason.strip():
        raise ValidationError({'reason': 'Explain why this code is being replaced.'})
    locked = LabelCode.objects.select_for_update().select_related('identity', 'identity__target_content_type').get(pk=code.pk)
    if locked.status != LabelCode.Status.ACTIVE:
        raise ValidationError({'code': 'Only an active code can be replaced.'})
    key = (locked.identity.target_content_type.app_label, locked.identity.target_content_type.model)
    locked.status = LabelCode.Status.REPLACED
    locked.retired_by = user
    locked.retired_at = timezone.now()
    locked.retirement_reason = reason.strip()
    locked.save()
    replacement = LabelCode.objects.create(
        workspace=locked.workspace,
        identity=locked.identity,
        code=_new_code(TARGET_PREFIXES[key]),
        issued_by=user,
    )
    locked.replacement = replacement
    locked.save(update_fields=['replacement'])
    return replacement


@transaction.atomic
def void_code(code, user, reason):
    """Retire a code without issuing another."""
    if not reason.strip():
        raise ValidationError({'reason': 'Explain why this code is being voided.'})
    locked = LabelCode.objects.select_for_update().get(pk=code.pk)
    if locked.status != LabelCode.Status.ACTIVE:
        raise ValidationError({'code': 'Only an active code can be voided.'})
    locked.status = LabelCode.Status.VOID
    locked.retired_by = user
    locked.retired_at = timezone.now()
    locked.retirement_reason = reason.strip()
    locked.save()
    return locked
