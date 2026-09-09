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
"""

from django.db import models

#: Both the empty values a catalog figure can hold. A blank field is not the
#: gardener disagreeing with the set, it is nobody having said anything yet,
#: so a set may fill it even on a record it did not install.
EMPTY = (None, '')


class ReferencedModel(models.Model):
    """Abstract catalog record whose facts may have come from a reference set."""

    reference_source = models.CharField(
        max_length=64,
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
