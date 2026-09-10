"""Telling what a reference set said about a crop from what a gardener knows.

A catalog can be started from a reference set rather than typed out from
nothing: a starter set fills in the families, crops and varieties a household
garden usually holds, with the spacings and day counts a seed catalogue prints.
Those figures are a starting point and no more. Reference maturity days were
written for somebody else's climate, and the day a gardener counts their own
the printed number stops being worth keeping.

So a record installed from a reference set remembers what that set said, field
by field, beside what the record now says. The two together answer both
questions worth asking, and neither needs a flag of its own: a field still
equal to what the set supplied is still the set's, and one that differs is the
gardener's, measured in their own garden. Editing marks nothing, because the
comparison is the mark.

That is what lets a set be installed a second time without arguing with the
gardener. Reinstalling fills what is still blank, refreshes what the set
supplied and nobody has touched, and leaves every figure somebody has made
their own exactly where it is. What the set says moves either way, so what it
says now is always readable next to what the record says.

A record the gardener made carries no source at all, which is the difference
between a catalog somebody built and one that arrived.

Installing one record is here rather than beside any particular set, because
the awkward half of it belongs to this idea rather than to what is being
installed: a set must never make the duplicate the warning would have told a
person they were typing, so a record is found by the normalized form of its
name and adopted, and a record somebody retired is left where they put it.
"""

from django.db import models

from .duplicates import normalized

#: How long a set's name may be. Stated here rather than only on the field,
#: because a document naming the set it carries has to be checked against the
#: same limit before anything is installed from it.
SOURCE_MAX_LENGTH = 64

#: Both the empty values a catalog figure can hold. A blank field is not the
#: gardener disagreeing with the set, it is nobody having said anything yet,
#: so a set may fill it even on a record it did not install.
EMPTY = (None, '')


class ReferencedModel(models.Model):
    """Abstract catalog record whose facts may have come from a reference set."""

    reference_source = models.CharField(
        max_length=SOURCE_MAX_LENGTH,
        blank=True,
        default='',
        editable=False,
        help_text=(
            'The reference set this record was installed from. Blank on a '
            'record the gardener made.'
        ),
    )
    reference_values = models.JSONField(
        default=dict,
        editable=False,
        help_text=(
            'What that set says about this record, field by field. A field '
            "still equal to it is still the set's; one that differs is the "
            "gardener's own."
        ),
    )

    class Meta:
        abstract = True

    def reference_fields(self):
        """Return the fields still saying exactly what the reference supplied.

        Read rather than stored, so a figure that stops being the set's the
        moment it is edited, and there is no second write to forget.
        """
        return sorted(
            name for name, value in self.reference_values.items()
            if getattr(self, name, None) == value
        )


def reference_writable(record, name):
    """Say whether a reference set may write this field.

    It may while nobody has said anything yet, and while the field still says
    what the set last supplied. Anything else is the gardener's answer to the
    same question, and a set that overwrote it would be telling them their own
    garden is wrong.
    """
    current = getattr(record, name, None)
    if current in EMPTY:
        return True
    return current == record.reference_values.get(name)


def apply_reference(record, source, values):
    """Write what a reference set says onto a record, keeping what is not its.

    ``reference_values`` takes every supplied figure whether or not the field
    itself moved, because it says what the set holds rather than what the
    record does. Returns the fields this actually changed, so a caller can say
    what installing did.
    """
    changed = []
    for name, value in values.items():
        if reference_writable(record, name) and getattr(record, name, None) != value:
            setattr(record, name, value)
            changed.append(name)
    record.reference_source = source
    record.reference_values = dict(values)
    return changed


def matching_name(records, name):
    """Return the record already meaning this name, or None.

    Compared the way two catalog names are compared everywhere else, so a
    gardener's ``Tomatoes`` is a set's ``Tomato`` rather than a second one.
    That is the same rule ``common.duplicates`` would have applied had a person
    been typing the name instead of a set installing it.
    """
    wanted = normalized(name)
    for record in records:
        if normalized(record.name) == wanted:
            return record
    return None


def install_record(model, source, name, values, **scope):
    """Adopt or create one record and write what the set says onto it.

    A retired record is returned untouched. Retiring is something the gardener
    chose, and a set that quietly refreshed a crop somebody had put away would
    be arguing with that as surely as one that overwrote a figure they had
    measured. A caller that walks a hierarchy stops there, which is what keeps
    a set from leaving an active record under a retired one.
    """
    record = matching_name(model.objects.filter(**scope), name)
    if record is not None and not record.active:
        return record
    if record is None:
        record = model(name=name, **scope)
    apply_reference(record, source, values)
    record.save()
    return record
