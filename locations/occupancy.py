"""What is standing in a location, and whether one more thing fits.

Occupancy is measured in every dimension a location can express — trays,
plants, containers — and only the one dimension a location declares as its
capacity basis is compared against its limit. Unlike dimensions are never
compared: a bench counted in trays says nothing about how many loose pots fit
on it.

This module reaches back into `plantings` and `seedtrays` from function bodies
rather than at import time, so the `locations` app itself keeps depending only
on `workspaces`. It is the same one-way-at-load pattern `inventory.ledger`
already uses for `unit_is_in_use`, and the reason `.pylintrc` turns
`cyclic-import` off.
"""

from typing import NamedTuple

from django.core.exceptions import ValidationError

from .models import Location


class Occupancy(NamedTuple):
    """What a location holds, counted in each dimension that can measure it."""

    trays: int = 0
    plants: int = 0
    containers: int = 0

    def of(self, basis):
        """Return the count in the dimension a capacity basis measures."""
        return getattr(self, basis, 0)

    def __add__(self, other):
        return Occupancy(
            trays=self.trays + other.trays,
            plants=self.plants + other.plants,
            containers=self.containers + other.containers,
        )

    @property
    def is_empty(self):
        """Return whether nothing at all is standing here."""
        return not (self.trays or self.plants or self.containers)


#: What one placement adds to a location, per kind of thing being placed. A
#: tray of seedlings really is that many plants on the bench, so it counts in
#: both dimensions; it is not a container, and a loose pot is not a tray.
def tray_contribution(plant_count):
    """Return what placing one tray holding `plant_count` plants adds."""
    return Occupancy(trays=1, plants=plant_count, containers=0)


def plant_contribution():
    """Return what standing one plant somewhere adds.

    One directly placed plant counts as one container until task 54 records
    real containers and a single pot can hold several plants.
    """
    return Occupancy(trays=0, plants=1, containers=1)


def location_occupancy(location, subtree=False):
    """Count what is standing in a location, optionally including its children."""
    from plantings.models import SpecificPlantLocation  # pylint: disable=import-outside-toplevel
    from seedtrays.models import SeedTray  # pylint: disable=import-outside-toplevel

    if subtree:
        lookup, value = 'in', list(location.subtree().values_list('pk', flat=True))
    else:
        lookup, value = 'exact', location.pk

    trays = SeedTray.objects.filter(
        **{f'inventory_unit__current_location__{lookup}': value},
    )
    tray_count = trays.count()
    plants_in_trays = SpecificPlantLocation.objects.filter(
        ended__isnull=True,
        **{f'seed_tray_cell__tray__inventory_unit__current_location__{lookup}': value},
    ).count()
    standing_plants = SpecificPlantLocation.objects.filter(
        ended__isnull=True,
        **{f'location__{lookup}': value},
    ).count()

    return Occupancy(
        trays=tray_count,
        plants=plants_in_trays + standing_plants,
        containers=standing_plants,
    )


#: Singular forms of the capacity bases, for messages an operator reads.
_BASIS_NOUNS = {
    Location.CapacityBasis.TRAYS: 'tray',
    Location.CapacityBasis.CONTAINERS: 'container',
    Location.CapacityBasis.PLANTS: 'plant',
    Location.CapacityBasis.AREA: 'area',
}


def _counted_in(basis, count):
    """Name a basis in the number the sentence is about."""
    noun = _BASIS_NOUNS.get(basis, basis)
    return noun if count == 1 else f'{noun}s'


def _plain(value):
    """Render a capacity without the trailing zeros of its stored scale.

    A bench that holds two trays should say two, not 2.000. Decimal's own `g`
    format keeps the stored scale, and stripping zeros unconditionally would
    turn 100 into 1, so the decimal point has to be there before anything is
    removed.
    """
    text = f'{value:f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def capacity_chain(location):
    """Return the location and its ancestors that actually limit anything.

    Ordered outermost first, which is also ascending primary-key order along a
    path, so callers that lock these rows always take them in the same order
    and two placements racing into the same greenhouse cannot deadlock.
    """
    chain_ids = location.ancestor_ids + [location.pk]
    return list(
        Location.objects
        .filter(pk__in=chain_ids)
        .exclude(capacity_basis=Location.CapacityBasis.NONE)
        .order_by('pk'),
    )


def check_capacity(destination, contribution, override_reason='', lock=True):
    """Reject a placement that does not fit, or that the basis cannot measure.

    Every capacitated ancestor is checked, so a greenhouse capped at 200 trays
    caps its benches' total rather than only what stands directly in its aisle.
    An overrun is refused unless the caller supplies a reason, which its own
    record then keeps.
    """
    chain = capacity_chain(destination)
    if lock and chain:
        chain = list(
            Location.objects
            .select_for_update()
            .filter(pk__in=[limit.pk for limit in chain])
            .order_by('pk'),
        )

    for limit in chain:
        basis = limit.capacity_basis
        if basis not in Location.ENFORCED_BASES:
            # `area` is recorded for planning; nothing measures a footprint to
            # compare against it yet, so it limits nothing.
            continue
        adding = contribution.of(basis)
        if not adding:
            # A mismatch is only an error at the place being chosen: putting a
            # loose pot on a bench counted in trays is a category error worth
            # saying out loud. An ancestor counted in something else simply
            # does not constrain this — a greenhouse measured in trays has no
            # opinion about a potted plant standing on one of its benches.
            if limit.pk != destination.pk:
                continue
            raise ValidationError({
                'destination': (
                    f'{limit.name} is measured in {_counted_in(basis, 2)}, '
                    'which this does not occupy.'
                ),
            })
        if override_reason:
            continue
        used = location_occupancy(limit, subtree=True).of(basis)
        if used + adding > limit.capacity_value:
            raise ValidationError({
                'destination': (
                    f'{limit.name} holds {_plain(limit.capacity_value)} '
                    f'{_counted_in(basis, limit.capacity_value)} and already has {used}. '
                    'Record a reason to place this anyway.'
                ),
            })


def blocking_occupancy(location):
    """Return what still stands in a location's subtree, or None when empty."""
    occupancy = location_occupancy(location, subtree=True)
    return None if occupancy.is_empty else occupancy
