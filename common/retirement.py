"""Retiring a catalog record without taking it out of history.

A gardener who mistypes a variety, changes seed supplier, or stops growing a
crop needs it gone from every selector while every planting, harvest, and
receipt that already names it still reads the same. Deleting cannot do that,
so a catalog record is retired instead: it keeps its identity and everything
pointing at it, and only stops being offered as a choice.

Retirement follows the catalog's own chain. A record may be active only while
everything it hangs off is active, which is one invariant seen from both ends:
a record cannot be restored under a retired parent, and a record cannot be
retired while something active still hangs off it. Retiring never cascades,
because an entry disappearing from a selector should always be something
somebody chose.
"""

import copy

from django.core.exceptions import ValidationError
from django.db import models
from rest_framework.exceptions import ValidationError as RestValidationError

from .rest_query import parse_boolean


class RetirableModel(models.Model):
    """Abstract catalog identity that is retired rather than deleted."""

    active = models.BooleanField(
        default=True,
        help_text=(
            'Retired records stay readable everywhere they are already '
            'recorded, but are no longer offered as choices.'
        ),
    )

    #: Names of the forward relations this record hangs off. It cannot be
    #: active while one of them is retired.
    retirement_parents = ()

    #: ``(related accessor, plural noun)`` pairs for the catalog records that
    #: hang off this one. Retiring is refused while any of them is active.
    retirement_dependants = ()

    class Meta:
        abstract = True

    def retirement_extra_errors(self):
        """Return reasons beyond the catalog chain that hold this state."""
        return []

    def clean(self):
        """Reject an activation state the catalog chain does not allow."""
        super().clean()
        errors = retirement_errors(self)
        if errors:
            raise ValidationError({'active': errors})


def retirement_errors(record):
    """Return why this record cannot hold the activation state it carries."""
    chain = _restore_errors(record) if record.active else _retire_errors(record)
    return chain + record.retirement_extra_errors()


def _restore_errors(record):
    """Refuse an active record whose parents are not all active."""
    retired = []
    for name in record.retirement_parents:
        if getattr(record, f'{name}_id', None) is None:
            continue
        parent = getattr(record, name)
        if isinstance(parent, RetirableModel) and not parent.active:
            retired.append(str(parent))
    if not retired:
        return []
    return [f"Restore {', '.join(retired)} first."]


def _retire_errors(record):
    """Refuse retiring a record that active catalog records still hang off."""
    if record.pk is None:
        return []
    errors = []
    for accessor, noun in record.retirement_dependants:
        blocking = [
            str(dependant)
            for dependant in getattr(record, accessor).filter(active=True)
        ]
        if blocking:
            errors.append(f"Retire these {noun} first: {', '.join(blocking)}.")
    return errors


def prospective_record(serializer, attrs):
    """Return the unsaved record a validated payload would leave behind."""
    model = serializer.Meta.model
    if serializer.instance is None:
        record = model()
    else:
        record = _detached_copy(serializer.instance)
    writable = {field.name for field in model._meta.concrete_fields}  # pylint: disable=protected-access
    for name, value in attrs.items():
        if name in writable:
            setattr(record, name, value)
    return record


def _detached_copy(record):
    """Copy a record far enough that validation cannot disturb the original."""
    clone = copy.copy(record)
    clone._state = copy.copy(record._state)  # pylint: disable=protected-access
    clone._state.fields_cache = dict(record._state.fields_cache)  # pylint: disable=protected-access
    return clone


class RetirementSerializerMixin:  # pylint: disable=too-few-public-methods
    """Report a refused activation change as an ordinary field error.

    ``ModelSerializer`` does not run ``Model.clean``, so without this the rule
    would only hold for admin and direct ORM writes. Both callers ask the same
    function rather than restating the chain.
    """

    def validate(self, attrs):
        """Validate the record this payload would save."""
        attrs = super().validate(attrs)
        errors = retirement_errors(prospective_record(self, attrs))
        if errors:
            raise RestValidationError({'active': errors})
        return attrs


class RetirableViewSetMixin:  # pylint: disable=too-few-public-methods
    """Serve retired catalog records alongside active ones unless asked.

    A catalog list is read for two different reasons: to offer choices, and to
    name the record an older planting or receipt points at. Filtering the
    collection by default would answer the first question by breaking the
    second, so a caller building a selector asks for ``?active=true``.
    """

    def get_queryset(self):
        """Narrow the collection by activation state when asked to."""
        queryset = super().get_queryset()
        active = parse_boolean(
            self.request.query_params.get('active'), 'active',
        )
        if active is None:
            return queryset
        return queryset.filter(active=active)
