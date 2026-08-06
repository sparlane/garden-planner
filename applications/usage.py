"""Suggest how much of an input a set of targets should consume.

These functions are pure: they take already-measured targets and an item's
configured usage, and return a quantity in the item's base unit. Nothing here
reads the database, so the same inputs always produce the same suggestion and
a posted document can be recalculated from its stored snapshot years later.

A suggestion is only ever a proposal. The operator confirms what was actually
used, and that confirmed amount is the inventory fact.
"""

from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError

from inventory.ledger import quantize_quantity
from inventory.models import InventoryItem
from inventory.units import UnitCode, UnitDimension, convert_standard_quantity, get_unit_definition


#: Dimension each rate-based usage divides by, matching the rules
#: `InventoryItem._validate_rate_based_usage` already enforces on the catalog.
RATE_DIMENSIONS = {
    InventoryItem.UsageBasis.SURFACE_AREA: UnitDimension.AREA,
    InventoryItem.UsageBasis.PER_UNIT: UnitDimension.COUNT,
}

#: Target types that carry a measured volume, area, or count respectively.
VOLUME_TARGETS = frozenset({'seed_tray_cell'})
AREA_TARGETS = frozenset({'garden_area', 'garden_bed', 'garden_row', 'garden_square'})
COUNT_TARGETS = frozenset({'specific_plant', 'inventory_unit', 'batch'})


class TargetInput(NamedTuple):
    """One measured thing an input was applied to."""

    target_type: str
    weight: Decimal = Decimal('1')
    cell_volume_ml: object = None
    area_m2: object = None
    label: str = ''


class UsageInputs(NamedTuple):
    """Everything the suggestion depends on, all of it snapshotted."""

    basis: str
    base_unit: str
    rate: object = None
    rate_unit: str = ''
    fixed_quantity: object = None
    fill_factor: object = None
    targets: tuple = ()


class UsageCalculation(NamedTuple):
    """A suggested quantity and the working that produced it."""

    basis: str
    basis_quantity: object
    basis_unit: str
    target_count: int
    calculated_base_quantity: object
    base_unit: str
    formula: str


def _require(condition, field, message):
    """Raise a field error unless a precondition holds."""
    if not condition:
        raise ValidationError({field: message})


def _weighted(targets, attribute):
    """Total one measured attribute across targets, scaled by each weight."""
    total = Decimal('0')
    for target in targets:
        value = getattr(target, attribute)
        _require(
            value is not None,
            'targets',
            f'{target.label or target.target_type} has no recorded '
            f'{attribute.replace("_", " ")}.',
        )
        total += Decimal(value) * Decimal(target.weight)
    return total


def _selected(targets, allowed, field_message):
    """Return the targets this basis measures, requiring at least one."""
    chosen = tuple(target for target in targets if target.target_type in allowed)
    _require(chosen, 'targets', field_message)
    return chosen


def _rate(inputs):
    """Return the configured rate and its unit, requiring both."""
    _require(inputs.rate is not None, 'configured_rate', 'This usage basis requires a rate.')
    _require(inputs.rate_unit, 'configured_rate_unit', 'This usage basis requires a rate unit.')
    _require(
        Decimal(inputs.rate) > 0,
        'configured_rate',
        'The rate must be greater than zero.',
    )
    dimension = get_unit_definition(inputs.rate_unit).dimension
    _require(
        dimension == RATE_DIMENSIONS[inputs.basis],
        'configured_rate_unit',
        'The rate unit has an incompatible dimension.',
    )
    return Decimal(inputs.rate), inputs.rate_unit


def _cell_volume_usage(inputs):
    """Total the volume of every selected cell and fill it to the given depth.

    There is no item rate here. A cell-volume item's catalog entry records no
    rate at all, because the quantity is the physical volume of the cells that
    were filled; cells of different sizes simply sum. Anything not going into a
    cell, such as spillage, is recorded separately as waste.
    """
    targets = _selected(
        inputs.targets,
        VOLUME_TARGETS,
        'Select the tray cells this input filled.',
    )
    fill_factor = Decimal(inputs.fill_factor if inputs.fill_factor is not None else 1)
    _require(
        fill_factor > 0,
        'fill_factor',
        'The fill factor must be greater than zero.',
    )
    millilitres = _weighted(targets, 'cell_volume_ml') * fill_factor
    calculated = convert_standard_quantity(
        millilitres,
        UnitCode.MILLILITRE,
        inputs.base_unit,
    )
    formula = (
        f'{len(targets)} cells totalling '
        f'{_trim(_weighted(targets, "cell_volume_ml"))} ml, filled to '
        f'{_trim(fill_factor)} = {_trim(quantize_quantity(calculated))} '
        f'{inputs.base_unit}'
    )
    return _result(inputs, millilitres, UnitCode.MILLILITRE, len(targets), calculated, formula)


def _surface_area_usage(inputs):
    """Apply a rate over the normalized area of every selected place."""
    targets = _selected(
        inputs.targets,
        AREA_TARGETS,
        'Select the ground this input covered.',
    )
    rate, rate_unit = _rate(inputs)
    area = _weighted(targets, 'area_m2')
    in_rate_unit = convert_standard_quantity(area, UnitCode.SQUARE_METRE, rate_unit)
    calculated = in_rate_unit * rate
    formula = (
        f'{_trim(area)} m2 at {_trim(rate)} {inputs.base_unit} per '
        f'{rate_unit} = {_trim(quantize_quantity(calculated))} {inputs.base_unit}'
    )
    return _result(inputs, area, UnitCode.SQUARE_METRE, len(targets), calculated, formula)


def _per_unit_usage(inputs):
    """Apply a rate once per selected plant or item."""
    targets = _selected(
        inputs.targets,
        COUNT_TARGETS,
        'Select the plants or items this input was applied to.',
    )
    rate, rate_unit = _rate(inputs)
    count = sum((Decimal(target.weight) for target in targets), Decimal('0'))
    calculated = count * rate
    formula = (
        f'{_trim(count)} x {_trim(rate)} {inputs.base_unit} per {rate_unit} '
        f'= {_trim(quantize_quantity(calculated))} {inputs.base_unit}'
    )
    return _result(inputs, count, rate_unit, len(targets), calculated, formula)


def _fixed_usage(inputs):
    """Use the item's fixed quantity regardless of how much was targeted.

    Targets are still recorded, because the point of the document is knowing
    what the input went on even when the amount does not depend on it.
    """
    _require(
        inputs.fixed_quantity is not None,
        'configured_fixed_quantity',
        'Fixed usage requires a default quantity.',
    )
    calculated = Decimal(inputs.fixed_quantity)
    _require(
        calculated > 0,
        'configured_fixed_quantity',
        'The fixed quantity must be greater than zero.',
    )
    formula = f'Fixed {_trim(calculated)} {inputs.base_unit}'
    return _result(inputs, None, '', len(inputs.targets), calculated, formula)


def _manual_usage(inputs):
    """Suggest nothing, because no formula applies to this item."""
    return _result(inputs, None, '', len(inputs.targets), None, 'Manual entry')


def _result(inputs, basis_quantity, basis_unit, target_count, calculated, formula):
    """Assemble one calculation, quantizing the suggestion for the ledger."""
    return UsageCalculation(
        basis=inputs.basis,
        basis_quantity=None if basis_quantity is None else quantize_quantity(basis_quantity),
        basis_unit=basis_unit,
        target_count=target_count,
        calculated_base_quantity=None if calculated is None else quantize_quantity(calculated),
        base_unit=inputs.base_unit,
        formula=formula,
    )


def _trim(value):
    """Render a decimal without the trailing zeros a fixed column carries."""
    text = f'{Decimal(value):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


CALCULATORS = {
    InventoryItem.UsageBasis.CELL_VOLUME: _cell_volume_usage,
    InventoryItem.UsageBasis.SURFACE_AREA: _surface_area_usage,
    InventoryItem.UsageBasis.PER_UNIT: _per_unit_usage,
    InventoryItem.UsageBasis.FIXED: _fixed_usage,
    InventoryItem.UsageBasis.MANUAL: _manual_usage,
}


def calculate_usage(inputs):
    """Return the suggested consumption for one basis and set of targets."""
    calculator = CALCULATORS.get(inputs.basis)
    _require(calculator is not None, 'usage_basis', 'Select a supported usage basis.')
    get_unit_definition(inputs.base_unit)
    return calculator(inputs)


def override_required(calculated, applied, tolerance_percent, tolerance_floor):
    """Return whether the gap between suggestion and fact must be explained.

    Both bounds have to be exceeded. The percentage alone would demand prose
    for a third of a millilitre on a small line, and the floor alone would let
    a large line drift by a wide margin unremarked, so each one grounds the
    other.

    A manual line has no suggestion to differ from, so nothing is required.
    """
    if calculated is None:
        return False
    difference = abs(Decimal(applied) - Decimal(calculated))
    if difference <= Decimal(tolerance_floor):
        return False
    allowed = Decimal(calculated) * Decimal(tolerance_percent) / Decimal('100')
    return difference > allowed


def workspace_override_required(workspace, calculated, applied):
    """Apply one workspace's configured override tolerance."""
    return override_required(
        calculated,
        applied,
        workspace.override_tolerance_percent,
        workspace.override_tolerance_floor,
    )


def item_usage_inputs(item, targets, basis=None, fill_factor=None):
    """Build calculation inputs from an item's configured usage.

    Reading the catalog happens here, once, so the caller can store what it
    read and recalculate from the stored copy afterwards.
    """
    return UsageInputs(
        basis=basis or item.default_usage_basis,
        base_unit=item.base_unit,
        rate=item.default_usage_rate,
        rate_unit=item.usage_rate_unit or '',
        fixed_quantity=item.default_fixed_quantity,
        fill_factor=fill_factor,
        targets=tuple(targets),
    )
