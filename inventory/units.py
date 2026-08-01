"""Controlled measurement units and exact standard conversions."""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class UnitDimension(models.TextChoices):
    """Physical dimensions understood by inventory calculations."""

    COUNT = 'count', 'Count'
    VOLUME = 'volume', 'Volume'
    MASS = 'mass', 'Mass'
    AREA = 'area', 'Area'


class UnitCode(models.TextChoices):
    """Stable codes accepted by inventory APIs and persisted records."""

    EACH = 'each', 'Each'
    SEED = 'seed', 'Seed'
    SEED_CLUSTER = 'seed_cluster', 'Seed cluster'
    MILLILITRE = 'ml', 'Millilitre'
    LITRE = 'l', 'Litre'
    GRAM = 'g', 'Gram'
    KILOGRAM = 'kg', 'Kilogram'
    SQUARE_METRE = 'm2', 'Square metre'


@dataclass(frozen=True)
class UnitDefinition:
    """Metadata and exact reference conversion for one controlled unit."""

    code: str
    label: str
    dimension: str
    conversion_family: str
    reference_unit: str
    to_reference_multiplier: Decimal


UNIT_DEFINITIONS = {
    UnitCode.EACH: UnitDefinition(
        UnitCode.EACH,
        UnitCode.EACH.label,
        UnitDimension.COUNT,
        'each',
        UnitCode.EACH,
        Decimal('1'),
    ),
    UnitCode.SEED: UnitDefinition(
        UnitCode.SEED,
        UnitCode.SEED.label,
        UnitDimension.COUNT,
        'seed',
        UnitCode.SEED,
        Decimal('1'),
    ),
    UnitCode.SEED_CLUSTER: UnitDefinition(
        UnitCode.SEED_CLUSTER,
        UnitCode.SEED_CLUSTER.label,
        UnitDimension.COUNT,
        'seed_cluster',
        UnitCode.SEED_CLUSTER,
        Decimal('1'),
    ),
    UnitCode.MILLILITRE: UnitDefinition(
        UnitCode.MILLILITRE,
        UnitCode.MILLILITRE.label,
        UnitDimension.VOLUME,
        'metric_volume',
        UnitCode.MILLILITRE,
        Decimal('1'),
    ),
    UnitCode.LITRE: UnitDefinition(
        UnitCode.LITRE,
        UnitCode.LITRE.label,
        UnitDimension.VOLUME,
        'metric_volume',
        UnitCode.MILLILITRE,
        Decimal('1000'),
    ),
    UnitCode.GRAM: UnitDefinition(
        UnitCode.GRAM,
        UnitCode.GRAM.label,
        UnitDimension.MASS,
        'metric_mass',
        UnitCode.GRAM,
        Decimal('1'),
    ),
    UnitCode.KILOGRAM: UnitDefinition(
        UnitCode.KILOGRAM,
        UnitCode.KILOGRAM.label,
        UnitDimension.MASS,
        'metric_mass',
        UnitCode.GRAM,
        Decimal('1000'),
    ),
    UnitCode.SQUARE_METRE: UnitDefinition(
        UnitCode.SQUARE_METRE,
        UnitCode.SQUARE_METRE.label,
        UnitDimension.AREA,
        'metric_area',
        UnitCode.SQUARE_METRE,
        Decimal('1'),
    ),
}


def get_unit_definition(code):
    """Return controlled metadata or raise a field-friendly validation error."""
    try:
        return UNIT_DEFINITIONS[code]
    except KeyError as exc:
        raise ValidationError(f'Unknown inventory unit code: {code}.') from exc


def convert_standard_quantity(
    quantity: Decimal | str,
    from_unit: str,
    to_unit: str,
) -> Decimal:
    """Convert an exact Decimal or decimal string between compatible units."""
    source = get_unit_definition(from_unit)
    target = get_unit_definition(to_unit)
    if source.conversion_family != target.conversion_family:
        raise ValidationError(
            f'{source.code} cannot be converted to {target.code}.',
        )
    if isinstance(quantity, Decimal):
        value = quantity
    elif isinstance(quantity, str):
        try:
            value = Decimal(quantity)
        except (ArithmeticError, ValueError) as exc:
            raise ValidationError(
                f'Invalid quantity "{quantity}" for unit conversion.',
            ) from exc
    else:
        raise ValidationError(
            'Quantity must be a Decimal or string to preserve decimal precision.',
        )
    if not value.is_finite():
        raise ValidationError(
            f'Invalid quantity "{quantity}" for unit conversion.',
        )
    return value * source.to_reference_multiplier / target.to_reference_multiplier
