"""Resolve variety overrides against their plant-level cultivation defaults."""


def variety_metadata_value(variety, field_name):
    """Return a variety value when set, otherwise the matching plant value."""
    value = getattr(variety, field_name)
    if value is not None:
        return value
    return getattr(variety.plant, field_name)


def variety_days(variety, prefix):
    """Return one effective minimum/maximum day range."""
    return (
        variety_metadata_value(variety, f'{prefix}_days_min'),
        variety_metadata_value(variety, f'{prefix}_days_max'),
    )
