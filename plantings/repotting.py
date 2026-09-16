"""Stand one selected seedling in each named pot, as a single repotting run.

A tray comes out of the propagation house all at once, and the bench of pots
it goes into was filled all at once: `seedtrays.container_fills` opens one
claim over a run of numbered pots. Repotting is the same shape of work in the
other direction, so it is one request here rather than a move per seedling —
a half-finished run would leave the operator reading a grid to find out which
seedlings are still in the tray.

Each pairing is still its own placement. A numbered pot owns its media history
and its cost, which is the whole reason it is numbered, and the plant that
stands in it captures the pot's open fill the way a single move does.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import InventoryUnit

from .models import SpecificPlant, SpecificPlantLocation
from .movement import lock_placement_containers, move_specific_plant


# What one run may hold. Every pairing costs a handful of queries under row
# locks held until the whole run commits, so an unbounded selection would hold
# the pot ledger against every other writer for as long as it took to walk it.
# A tray is the unit of work this serves and no tray has this many cells.
MAX_REPOTTED_PLANTS = 500


def _pairing_errors(pairings):
    """Refuse a pairing table that does not describe one plant per pot."""
    if not pairings:
        return ['Pair at least one plant with a pot.']
    if len(pairings) > MAX_REPOTTED_PLANTS:
        return [f'Repot at most {MAX_REPOTTED_PLANTS} plants in one run.']
    errors = []
    for field, index in (('plant', 0), ('pot', 1)):
        counts = {}
        for pairing in pairings:
            counts[pairing[index]] = counts.get(pairing[index], 0) + 1
        repeated = sorted(value for value, count in counts.items() if count > 1)
        if repeated:
            named = ', '.join(f'#{value}' for value in repeated)
            # Several plants may share one pot, and a single move still says
            # so. It is not what a paired run means: two seedlings against one
            # number is the number typed twice, and quietly potting them
            # together would be the one reading nobody asked for.
            errors.append(f'Pair each {field} once: {named} appears more than once.')
    return errors


@transaction.atomic
def repot_into_pots(workspace, user, pairings, *, started=None, notes='', override_reason=''):  # pylint: disable=too-many-arguments
    """Move each paired plant into its own numbered pot, all or nothing.

    Locks every plant and then every source and destination container before
    the first move, in the order `counted_fills` takes them, so two runs over
    an overlapping bench queue behind each other rather than deadlock.
    """
    pairings = [(int(plant), int(pot)) for plant, pot in pairings]
    errors = _pairing_errors(pairings)
    if errors:
        raise ValidationError({'placements': errors})
    plant_ids = sorted(plant for plant, _pot in pairings)
    pot_ids = sorted(pot for _plant, pot in pairings)
    plants = {
        plant.pk: plant
        for plant in SpecificPlant.objects.select_for_update()
        .filter(workspace=workspace, pk__in=plant_ids).order_by('pk')
    }
    missing = [plant_id for plant_id in plant_ids if plant_id not in plants]
    if missing:
        raise ValidationError({'placements': f'No such plants in this workspace: {missing}.'})
    pots = {
        unit.pk: unit
        for unit in InventoryUnit.objects.filter(workspace=workspace, pk__in=pot_ids)
    }
    missing = [pot_id for pot_id in pot_ids if pot_id not in pots]
    if missing:
        raise ValidationError({'placements': f'No such containers in this workspace: {missing}.'})
    started = started or timezone.now()
    ordered = sorted(pairings)
    destinations = {
        plant: {
            'location_type': SpecificPlantLocation.CONTAINER_UNIT,
            'container_unit': pots[pot], 'started': started,
            'notes': notes, 'override_reason': override_reason,
        }
        for plant, pot in ordered
    }
    placements = list(
        SpecificPlantLocation.objects.select_for_update()
        .filter(specific_plant__in=plants.values(), ended__isnull=True).order_by('pk')
    )
    lock_placement_containers(placements, destinations.values())
    return [move_specific_plant(plants[plant], destinations[plant], user) for plant, _pot in ordered]
