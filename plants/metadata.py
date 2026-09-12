"""Resolve variety overrides against their plant-level cultivation defaults.

A blank figure on a variety is not a missing figure. Every sowing date,
maturity date and projected task is worked out through here, so the crop's
figure is already the one in force -- what a variety carries of its own is an
override rather than the whole of its planning data. Only the catalog screens
were still calling a blank one unknown, which is why ``variety_inheritance``
exists: it answers, field by field, what the value is and whose it is, from the
same place the dates come from.
"""

#: The cultivation figures a variety may override, in the order a screen reads
#: them. Nothing else on a variety inherits: a name and a note describe that
#: variety and no more, and a blank one means blank.
INHERITED_FIELDS = (
    'spacing',
    'inter_row_spacing',
    'plants_per_square_foot',
    'germination_days_min',
    'germination_days_max',
    'maturity_days_min',
    'maturity_days_max',
    'maturity_basis',
)


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


def variety_inheritance(variety):
    """Return, per cultivation figure, the value in force and whose it is.

    ``inherited`` says the variety left the field blank and is being planned by
    its crop's figure, which is the one thing a screen cannot work out from the
    variety alone: a blank and a figure that happens to match the crop's mean
    different things the next time the crop is corrected.
    """
    return {
        field_name: {
            'value': variety_metadata_value(variety, field_name),
            'inherited': getattr(variety, field_name) is None,
        }
        for field_name in INHERITED_FIELDS
    }
