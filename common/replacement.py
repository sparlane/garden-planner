"""Replacing a catalog record that has stopped being right about what it names.

A merge says two records were always the same thing. A replacement says one
record has become a different thing: a beetroot packet received in seeds has to
be counted in clusters from now on, or an entry was filed under the wrong
supplier after a season of receipts had already been written against it.
Retirement alone cannot express either — it takes the entry out of the
selectors and leaves nothing to choose instead.

So a replacement is the mirror of a merge. A merge moves references and never
changes what one of them means; a replacement changes what a record means and
therefore never moves a reference. Everything here follows from that:

* Nothing moves. Every packet, receipt and sowing stays on the record it was
  written against, because that record still says exactly what it said when
  they were written. The preview reports what stays rather than what goes.
* The successor starts empty and active, carrying the corrected identity. It
  is what gets offered from now on, and the record it supersedes is retired
  pointing at it, so a reader who searches for the old name finds where the
  catalog went.
* A record is superseded rather than corrected only once something has been
  posted against it. Until then the correction is an ordinary edit, which is
  what ``identity_fields`` and ``identity_locked`` decide between: they name
  the fields that stop being editable, and when they stop.

The successor is written as a correction rather than as a whole new record —
the request names what should read differently and everything else carries
over — so replacing an entry cannot quietly drop the half of it nobody thought
to mention.
"""

from django.db import transaction
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.response import Response

from .references import incoming_references
from .retirement import RetirableModel, handoff_field, retire_errors

#: What an in-place edit is refused with once the record has posted history.
LOCKED_IDENTITY_MESSAGE = (
    'Replace the record instead of changing this after stock has been posted.'
)


class ReplaceableModel(RetirableModel):
    """Abstract catalog identity that is superseded rather than rewritten."""

    replaced_by = handoff_field(
        'replaces',
        'The record that supersedes this one. Everything already recorded '
        'stays here, because it was recorded against what this one said.',
    )

    #: The fields that say what this record *is* rather than describing it.
    #: They stop being editable once ``identity_locked`` holds.
    identity_fields = ()

    class Meta:
        abstract = True

    def identity_locked(self):
        """Say whether posted history has frozen what this record names."""
        return False


def replacement_errors(source):
    """Return why this record cannot be superseded by a new one."""
    errors = []
    if source.replaced_by_id:
        errors.append(f'{source} was already replaced by {source.replaced_by}.')
    if getattr(source, 'merged_into_id', None):
        errors.append(f'{source} was merged into {source.merged_into}.')
    return errors + retire_errors(source)


def replacement_preview(source):
    """Describe what replacing this record would leave where it is."""
    return {
        'source': {'pk': source.pk, 'label': str(source)},
        'identity_locked': source.identity_locked(),
        'identity_fields': list(source.identity_fields),
        'stays': incoming_references(source),
        'blockers': replacement_errors(source),
    }


def replacement_values(inherited, changes):
    """Return the successor's fields: the record's own, with the changes named.

    A replacement is written as a correction, so anything the request does not
    name carries over from the record being superseded.
    """
    return {
        **inherited,
        **{
            name: value
            for name, value in changes.items()
            if name in inherited
        },
    }


@transaction.atomic
def replace_record(source, replacement):
    """Retire the record pointing at its successor, moving nothing."""
    source = type(source).objects.select_for_update().get(pk=source.pk)
    errors = replacement_errors(source)
    if errors:
        raise RestValidationError({'replacement': errors})
    stayed = incoming_references(source)
    source.replaced_by = replacement
    source.active = False
    source.save()
    return stayed


class ReplacementSerializerMixin:  # pylint: disable=too-few-public-methods
    """Refuse an in-place identity change once history has been posted.

    The refusal names the field the operator was editing rather than the
    record, because that is what a screen can put the message against, and it
    says what to do instead: the replacement route is the way through.
    """

    def validate(self, attrs):
        """Validate the change this payload would make to an existing record."""
        attrs = super().validate(attrs)
        record = self.instance
        if record is None or not record.identity_locked():
            return attrs
        errors = {
            name: LOCKED_IDENTITY_MESSAGE
            for name in record.identity_fields
            if name in attrs and attrs[name] != getattr(record, name)
        }
        if errors:
            raise RestValidationError(errors)
        return attrs


class ReplaceableViewSetMixin:  # pylint: disable=too-few-public-methods
    """Preview and carry out superseding one catalog record with a new one."""

    #: Representation keys the successor never inherits. Read-only fields are
    #: dropped as well, so only the writable half of the record carries over.
    #: ``active`` is named here because the successor is what gets offered
    #: instead, which is the point of making it.
    replacement_ignored_fields = ('active',)

    @action(detail=True, methods=['get', 'post'])
    def replace(self, request, pk=None):  # pylint: disable=unused-argument
        """Describe or carry out superseding this record with a new one.

        The preview and the replacement answer the same question and report the
        same refusals; only the method decides whether anything is written.
        """
        source = self.get_object()
        if request.method == 'GET':
            return Response(replacement_preview(source))
        return Response(self._replace(source, request))

    def _inheritable_values(self, source):
        """Return what the successor carries over unless the request says otherwise."""
        serializer = self.get_serializer(source)
        ignored = set(self.replacement_ignored_fields)
        ignored |= {
            name for name, field in serializer.fields.items() if field.read_only
        }
        return {
            name: value
            for name, value in serializer.data.items()
            if name not in ignored
        }

    @transaction.atomic
    def _replace(self, source, request):
        """Create the successor, then retire the record pointing at it."""
        inherited = self._inheritable_values(source)
        values = replacement_values(inherited, request.data)
        if values == inherited:
            raise RestValidationError({'replacement': [
                'Name what the replacement should say differently.',
            ]})
        serializer = self.get_serializer(data=values)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        stayed = replace_record(source, serializer.instance)
        source.refresh_from_db()
        return {
            'source': self.get_serializer(source).data,
            'replacement': self.get_serializer(serializer.instance).data,
            'stayed': stayed,
        }
