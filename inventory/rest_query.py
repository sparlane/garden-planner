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
    if value is None:
        return None
    parsed = serializers.DateField().run_validation(value)
    if parsed is None:
        raise ValidationError({field: 'Enter a valid date.'})
    return parsed


def parse_datetime(value, field):
    """Parse an optional ISO timestamp query parameter."""
    if value is None:
        return None
    parsed = serializers.DateTimeField().run_validation(value)
    if parsed is None:
        raise ValidationError({field: 'Enter a valid timestamp.'})
    return parsed
