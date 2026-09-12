"""Place selected plants in anonymous pots, one per pot, without numbering them."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from locations.models import Location
from locations.occupancy import capacity_chain
from seedtrays.models import SeedTrayGeneration

from .models import SpecificPlant, SpecificPlantLocation
from .movement import lock_placement_containers, move_specific_plant


@transaction.atomic
def plant_counted_fill(workspace, user, fill, plant_ids, *, started=None, override_reason=''):  # pylint: disable=too-many-arguments
    """Move one selected plant into each unused pot, all or nothing.

    The plant list is the only set of identities created here. Counted pots
    remain a lot claim, and each placement owns one original pot's media share.
    Lock all plants and source/destination stock before making the first move.
    """
    ids = sorted(set(plant_ids))
    if not ids:
        raise ValidationError({'plants': 'Choose at least one plant.'})
    plants = list(SpecificPlant.objects.select_for_update().filter(workspace=workspace, pk__in=ids).order_by('pk'))
    if len(plants) != len(ids):
        raise ValidationError({'plants': 'Choose plants in this workspace.'})
    fill = SeedTrayGeneration.objects.filter(workspace=workspace, pk=fill.pk, stock_lot__isnull=False).first()
    if fill is None:
        raise ValidationError({'container_fill': 'Choose a counted pot fill in this workspace.'})
    destination = {
        'location_type': SpecificPlantLocation.LOCATION,
        'location': fill.source_location, 'container_fill': fill,
        'started': started or timezone.now(), 'override_reason': override_reason,
    }
    limits = capacity_chain(fill.source_location)
    list(Location.objects.select_for_update().filter(pk__in=[limit.pk for limit in limits]).order_by('pk'))
    placements = list(SpecificPlantLocation.objects.select_for_update().filter(specific_plant__in=plants, ended__isnull=True))
    lock_placement_containers(placements, [destination])
    if fill.plant_locations.count() + len(plants) > fill.container_count:
        raise ValidationError({'container_fill': 'There are not enough unused pots in this fill for the selected plants.'})
    return [move_specific_plant(plant, destination, user) for plant in plants]
