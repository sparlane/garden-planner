"""Small strict parsers shared by inventory query-string filters."""

from rest_framework import serializers
from rest_framework.exceptions import ValidationError


def parse_boolean(value, field):
    """Parse an optional true/false query parameter."""
    if value is None:
        return None
    if value not in {'true', 'false'}:
        raise ValidationError({field: 'Use true or false.'})
    return value == 'true'


def parse_integer(value, field):
    """Parse an optional integer query parameter."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError({field: 'Use an integer ID.'}) from exc


def parse_date(value, field):
    """Parse an optional ISO date query parameter."""
    return _parse_field(serializers.DateField(), value, field, 'Enter a valid date.')


def parse_datetime(value, field):
    """Parse an optional ISO timestamp query parameter."""
    return _parse_field(
        serializers.DateTimeField(),
        value,
        field,
        'Enter a valid timestamp.',
    )


def _parse_field(serializer_field, value, field, message):
    """Run one field's validation, blaming the query parameter by name.

    The field's own error is raised without a key, which reads as a body error
    rather than a bad query parameter, so it is re-raised under the parameter
    name the caller supplied.
    """
    if value is None:
        return None
    try:
        parsed = serializer_field.run_validation(value)
    except ValidationError as exc:
        raise ValidationError({field: exc.detail}) from exc
    if parsed is None:
        raise ValidationError({field: message})
    return parsed
