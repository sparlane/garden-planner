"""Normalize garden geometry into physical area.

Garden placements and sizes are dimensionless integers on a grid. Reading one
as a length requires an operator to have said what a grid step measures, so
every function here refuses to guess: an area without a
:class:`~garden.models.GardenGeometryConfirmation` raises rather than assuming
that a historical integer meant metres.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    GardenArea,
    GardenBed,
    GardenGeometryConfirmation,
    GardenRow,
    GardenSquare,
)


#: Exact metre equivalents for one of each supported length unit. Inches and
#: feet are exact by definition, so no value here is an approximation.
LENGTH_UNIT_METRES = {
    GardenGeometryConfirmation.LengthUnit.MILLIMETRE: Decimal('0.001'),
    GardenGeometryConfirmation.LengthUnit.CENTIMETRE: Decimal('0.01'),
    GardenGeometryConfirmation.LengthUnit.METRE: Decimal('1'),
    GardenGeometryConfirmation.LengthUnit.INCH: Decimal('0.0254'),
    GardenGeometryConfirmation.LengthUnit.FOOT: Decimal('0.3048'),
}

#: Precision of a normalized area, matching the column that stores it.
AREA_QUANTUM = Decimal('0.000001')


def owning_area(geometry):
    """Return the area whose confirmed scale governs one piece of geometry."""
    if isinstance(geometry, GardenArea):
        return geometry
    if isinstance(geometry, GardenBed):
        return geometry.area
    if isinstance(geometry, (GardenRow, GardenSquare)):
        return geometry.bed.area
    raise ValidationError(
        {'target': 'That target does not describe a piece of garden geometry.'},
    )


def latest_confirmation(confirmations):
    """Return the newest of an already-loaded set of confirmations.

    Sorting in Python rather than the database lets a caller that prefetched an
    area's confirmations reuse them instead of issuing one query per area.
    """
    rows = sorted(confirmations, key=lambda row: (row.confirmed_at, row.pk))
    return rows[-1] if rows else None


def area_confirmation(area):
    """Return the newest confirmation for one area, or None while unconfirmed."""
    return area.geometry_confirmations.order_by('-confirmed_at', '-pk').first()


def is_confirmed(area):
    """Return whether an operator has stated what this area's grid step means."""
    return area_confirmation(area) is not None


def require_confirmation(area):
    """Return one area's governing confirmation, refusing to guess without it."""
    confirmation = area_confirmation(area)
    if confirmation is None:
        raise ValidationError({
            'target': (
                f'Confirm the physical length unit of garden area '
                f'"{area.name}" before measuring it.'
            ),
        })
    return confirmation


def metres_per_step(confirmation):
    """Return the physical length of one grid step for one confirmation."""
    return confirmation.cell_length * LENGTH_UNIT_METRES[confirmation.length_unit]


def metres_per_grid_step(area):
    """Return the physical length of one grid step for one area."""
    return metres_per_step(require_confirmation(area))


def measure(confirmation, geometry):
    """Return normalized area from an already-resolved confirmation.

    The scale is squared because ``size_x`` and ``size_y`` are both counts of
    the same grid step, so an area is steps squared times metres squared.
    """
    step = metres_per_step(confirmation)
    total = Decimal(geometry.size_x) * Decimal(geometry.size_y) * step * step
    return total.quantize(AREA_QUANTUM)


def square_metres(geometry):
    """Return one piece of geometry's normalized area in square metres."""
    return measure(require_confirmation(owning_area(geometry)), geometry)
