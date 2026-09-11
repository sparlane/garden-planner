"""A usage setting: the catalog record something else names by code.

A growth stage or a plant grade is a catalog record like a crop or a supplier
is, and it is wrong in the same three ways -- it stopped being used, it got
typed twice, or somebody is about to type it twice again -- so it takes the
same corrections, from ``common.catalog``. What it carries that a crop does
not is a stable code. The name is what an operator reads on a form; the code
is the handle everything else holds the record by, which is why
``plantings.signals`` finds the stages it seeds by code and re-finds them
every time a workspace is saved.

That is the whole of what this module adds, and the one rule that follows from
it: a code is written once and never again. A crop typed twice is one record
too many and either of them can be renamed; a setting typed twice is one code
too many, and re-cutting a code would tell everything holding the old one that
it had always meant something else. So the wrong one is merged into the right
one instead. The references move, and the code is kept on a retired record
pointing at where it went, which is exactly what something still holding it is
owed -- the same sentence ``common.merging`` opens with, reached from the one
field a usage setting has that a crop does not.

Read against ``common.replacement``, which refuses an in-place change for the
mirror-image reason, the pair is the whole difference between a record that
owns a stock identity and one that owns a name other records were written
with. A seed entry's supplier is frozen by what a packet was received as, and
correcting it starts a second entry. A setting's code is frozen by what names
it, and correcting it merges into one.
"""

from django.core.exceptions import ValidationError
from django.db import models
from rest_framework.exceptions import ValidationError as RestValidationError

from .merging import MergeableModel


def normalize_code(code):
    """Return the form a stable code is stored and compared in."""
    return code.strip().lower()


class CodedSettingModel(MergeableModel):
    """Abstract workspace setting chosen on a form and held by its code."""

    code = models.CharField(
        max_length=64,
        help_text=(
            'The stable handle everything else names this setting by. It is '
            'written once; a wrong one is corrected by merging into the '
            'record that carries the right one.'
        ),
    )
    name = models.CharField(max_length=128)
    display_order = models.IntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ['display_order', 'name', 'pk']

    def search_names(self):
        """Return the names this setting is found by.

        Its own and its code. Nothing hangs off a usage setting, so there is
        nothing further down the catalog to reach, but the code is worth
        searching by: somebody who has met the record in a seeded default or a
        stored rule has met its code rather than its name, and typing what
        they were shown should find it.
        """
        yield self.name
        yield self.code

    def __str__(self):
        return self.name

    def clean(self):
        """Normalize the stable code and refuse an empty one."""
        super().clean()
        self.code = normalize_code(self.code)
        if not self.code:
            raise ValidationError({'code': 'A stable code is required.'})

    def save(self, *args, **kwargs):
        """Validate direct ORM writes as well as REST writes."""
        self.full_clean()
        super().save(*args, **kwargs)


class StableCodeSerializerMixin:  # pylint: disable=too-few-public-methods
    """Refuse rewriting the code other records hold this setting by.

    The refusal names the correction it is owed, the way an in-place change to
    a frozen seed entry names the replacement route: what somebody wanting a
    different code actually wants is a second setting under it, with everything
    recorded against this one moved across.
    """

    def validate(self, attrs):
        """Reject a payload that would re-cut this setting's stable code."""
        attrs = super().validate(attrs)
        if self.instance is None or 'code' not in attrs:
            return attrs
        if normalize_code(attrs['code']) != self.instance.code:
            raise RestValidationError({'code': [
                'A stable code cannot be changed. Add the setting under the '
                'code you want and merge this one into it.',
            ]})
        return attrs
