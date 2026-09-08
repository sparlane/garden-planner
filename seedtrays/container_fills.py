"""Open pot fills against available containers without consuming the containers.

The shared generation owns the fill identity. Media application and plant
departures will use that identity; opening itself only claims empty pots.
"""

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.ledger import (
    balance_is_known,
    lock_lots,
    lock_units,
    unit_is_in_use,
    unit_physical_state,
    unpromised_bulk,
)
from inventory.models import InventoryItem
from locations.models import Location
from sales.models import SalesOrderAllocation

from .models import SeedTrayGeneration, SeedTrayGenerationEvent


def _require_location(workspace, location):
    """Require a known, active nursery location for the pots being filled."""
    if location is None or location.workspace_id != workspace.pk or not location.active:
        raise ValidationError({'source_location': 'Choose an active location in this workspace.'})
    if location.location_type == Location.LocationType.QUARANTINE or location.code == 'SYSTEM-TRAY-UNKNOWN':
        raise ValidationError({'source_location': 'Reconcile or release these containers before filling them.'})


def _require_item(item):
    """Keep tray opening on the existing tray service."""
    if not item.active or item.category != InventoryItem.Category.POT_CONTAINER:
        raise ValidationError({'container': 'Choose an active pot container item.'})


def _record_open(fill, user):
    """Save one validated identity and its opening fact in the same transaction."""
    fill.save()
    SeedTrayGenerationEvent.objects.create(
        generation=fill,
        event_type=SeedTrayGenerationEvent.EventType.OPENED,
        occurred_at=fill.opened_at,
        reason='Pots filled.',
        created_by=user if user is not None and user.is_authenticated else None,
    )
    return fill


def _new_fill(workspace, user, opened_at, notes, **target):
    """Construct the common audited fill without assigning anonymous pot identities."""
    return SeedTrayGeneration(
        workspace=workspace, code=f'POTS-{uuid4().hex}', sequence=1,
        opened_at=opened_at or timezone.now(), notes=notes,
        created_by=user if user is not None and user.is_authenticated else None,
        **target,
    )


@transaction.atomic
def open_counted_fill(workspace, user, lot, location, count, *, opened_at=None, notes=''):  # pylint: disable=too-many-arguments
    """Claim whole empty pots, serialized with reservations, numbering and stock moves.

    The count remains on hand; the open fill removes it from unpromised stock.
    No container consumption or change to the pot's acquisition cost is posted.
    """
    lot = lock_lots(workspace, [lot.pk])[lot.pk]
    _require_item(lot.item)
    _require_location(workspace, location)
    if not balance_is_known(lot):
        raise ValidationError({'stock_lot': 'Count the stock before opening a pot fill.'})
    fill = _new_fill(
        workspace, user, opened_at, notes,
        stock_lot=lot, source_location=location, container_count=count,
    )
    # Validate before IntegerField can silently truncate a fractional count.
    fill.full_clean()
    available = unpromised_bulk(lot, location)
    if fill.container_count > available:
        raise ValidationError({'container_count': f'Only {available} empty, unpromised pots are available here.'})
    return _record_open(fill, user)


@transaction.atomic
def open_numbered_fill(workspace, user, unit, *, opened_at=None, notes=''):
    """Claim an empty numbered pot, locking even its first fill against competing claims.

    Only the unit lock is needed: this action changes neither the anonymous
    pool nor the ledger. Sales and physical unit actions take this lock too.
    """
    unit = lock_units(workspace, [unit.pk])[unit.pk]
    _require_item(unit.item)
    _require_location(workspace, unit.current_location)
    if not unit.active or unit_physical_state(unit) != 'available':
        raise ValidationError({'inventory_unit': 'The container is not available to fill.'})
    if unit_is_in_use(unit):
        raise ValidationError({'inventory_unit': 'Move the plants before opening a new fill.'})
    if SalesOrderAllocation.objects.filter(
        inventory_unit=unit, status=SalesOrderAllocation.Status.RESERVED,
    ).exists():
        raise ValidationError({'inventory_unit': 'The container is reserved for an order.'})
    previous = unit.container_fills.order_by('-sequence').first()
    if unit.container_fills.filter(status=SeedTrayGeneration.Status.OPEN).exists():
        raise ValidationError({'inventory_unit': 'Clean the current fill before filling this container again.'})
    fill = _new_fill(workspace, user, opened_at, notes, inventory_unit=unit)
    fill.sequence = previous.sequence + 1 if previous else 1
    return _record_open(fill, user)
