"""Open pot fills against available containers without consuming the containers.

The shared generation owns the fill identity. Media application and plant
departures will use that identity; opening itself only claims empty pots.
"""

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from applications.models import InputApplication
from inventory.ledger import (
    balance_is_known,
    lock_lots,
    lock_units,
    quantize_quantity,
    reverse_tray_generation_movements,
    unit_is_in_use,
    unit_physical_state,
    unpromised_bulk,
)
from inventory.models import InventoryItem
from locations.models import Location
from sales.models import SalesOrderAllocation

from .generations import CloseRequest, contents_digest, match_residual_quantities, write_residual
from .models import PotFillResidualCorrection, SeedTrayGeneration, SeedTrayGenerationEvent
from .pot_media import pot_fill_contents, pot_fill_remaining_media


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


def lock_pot_fills(workspace, fill_ids):
    """Lock pot stock, numbered units, then fills, consistently for every writer.

    Media lots come afterwards. Pot media documents contain only growing-media
    lines, so no document can acquire these container locks after a media lock.
    """
    identifiers = sorted(set(fill_ids))
    fills = list(SeedTrayGeneration.objects.filter(workspace=workspace, pk__in=identifiers).order_by('pk'))
    if len(fills) != len(identifiers) or any(fill.tray_id for fill in fills):
        raise ValidationError({'container_fill': 'Choose pot fills in this workspace.'})
    lock_lots(workspace, [fill.stock_lot_id for fill in fills if fill.stock_lot_id])
    lock_units(workspace, [fill.inventory_unit_id for fill in fills if fill.inventory_unit_id])
    return list(SeedTrayGeneration.objects.select_for_update(of=('self',)).filter(pk__in=identifiers).order_by('pk'))


def clean_empty_fill(workspace, user, fill, *, reason, occurred_at=None):
    """Clean without media dispositions only when no posted media remains."""
    return clean_pot_fill(workspace, user, fill, CloseRequest(reason=reason, occurred_at=occurred_at))


@transaction.atomic
def clean_pot_fill(workspace, user, fill, request):
    """Clean empty pots, disposing only of media not taken by departed plants.

    Numbered fills whose complete participation has departed retain that media
    in their departure reports. Counted fills dispose of the rounded remainder
    in their unplanted pots. No pot stock movement is posted: closing releases
    only the claim left after the plants' departures.
    """
    if not request.reason or not request.reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})
    if request.plants or request.seeds or request.open_next:
        raise ValidationError({'fill': 'This clean accepts media dispositions only.'})
    fill = lock_pot_fills(workspace, [fill.pk])[0]
    _require_cleanable_pot(fill)
    occurred_at = request.occurred_at or timezone.now()
    contents = pot_fill_contents(fill)
    remaining = pot_fill_remaining_media(fill)
    if request.digest is not None and request.digest != contents_digest({'plants': [], 'seeds': [], 'media': remaining}):
        raise ValidationError({'digest': 'The fill changed after this clean was prepared. Review it again.'})
    if occurred_at < fill.opened_at or any(occurred_at < row['latest_application'] for row in contents) or fill.plant_locations.filter(ended__gt=occurred_at).exists():
        raise ValidationError({'occurred_at': 'The clean cannot precede opening, media application or a plant departure.'})
    totals = {row['lot'].pk: row['base_quantity'] for row in remaining}
    match_residual_quantities(totals, request.media, 'lot_id', 'media', ('waste', 'reclaimed'))
    lots = lock_lots(workspace, totals)
    for row in request.media:
        write_residual(fill, user, {
            'kind': 'media', 'disposition': row.disposition,
            'lot': lots[row.lot_id], 'quantity': quantize_quantity(row.quantity),
            'destination': row.destination, 'reason': row.reason,
        }, occurred_at)
    actor = user if user is not None and user.is_authenticated else None
    SeedTrayGeneration.objects.filter(pk=fill.pk).update(
        status=SeedTrayGeneration.Status.CLOSED, closed_at=occurred_at,
        close_reason=request.reason.strip(), closed_by=actor, updated=timezone.now(),
    )
    SeedTrayGenerationEvent.objects.create(
        generation=fill, event_type=SeedTrayGenerationEvent.EventType.CLOSED,
        occurred_at=occurred_at, reason=request.reason.strip(), created_by=actor,
    )
    fill.refresh_from_db()
    return fill


def _require_cleanable_pot(fill):
    """Refuse a clean that would lose unaccounted contents or prior history."""
    if fill.status != SeedTrayGeneration.Status.OPEN:
        raise ValidationError({'fill': 'This fill is already closed.'})
    if fill.review_state != SeedTrayGeneration.ReviewState.NONE:
        raise ValidationError({'fill': 'Review this fill before cleaning it.'})
    if fill.application_targets.exists() or fill.residuals.filter(pot_correction__isnull=True).exists() or fill.sowings.exists():
        raise ValidationError({'fill': 'This fill has recorded contents requiring a different clean workflow.'})
    if fill.stock_lot_id:
        if fill.plant_locations.filter(ended__isnull=True).exists():
            raise ValidationError({'fill': 'Move the plants before cleaning this fill.'})
        if fill.plant_locations.count() > fill.container_count:
            raise ValidationError({'fill': 'The recorded participants exceed the original pot count.'})
    if fill.inventory_unit_id:
        unit = fill.inventory_unit
        if unit_is_in_use(unit):
            raise ValidationError({'fill': 'Move the plants before cleaning this fill.'})
        if unit.application_targets.exclude(line__application__status=InputApplication.Status.REVERSED).exists():
            raise ValidationError({'fill': 'Resolve the container input applications before cleaning this fill.'})
        history = unit.standing_plants.filter(
            Q(ended__isnull=True) | Q(ended__gte=fill.opened_at),
        )
        participants = fill.plant_locations.all()
        if participants.filter(ended__isnull=True).exists():
            raise ValidationError({'fill': 'Move the plants before cleaning this fill.'})
        if fill.plant_share_count and participants.count() != fill.plant_share_count:
            raise ValidationError({'fill': 'The recorded participants do not match the frozen media shares.'})
        unknown_history = history.exclude(container_fill=fill).exists() or (participants.exists() and not fill.plant_share_count)
        if pot_fill_contents(fill) and unknown_history:
            raise ValidationError({'fill': 'This container has plant history requiring fill departure accounting.'})


def _require_reclaimable_containers(fill):
    """A correction must claim the pots again before it can restore their media."""
    if fill.stock_lot_id:
        _require_item(fill.stock_lot.item)
        _require_location(fill.workspace, fill.source_location)
        remaining = fill.container_count - fill.plant_locations.filter(ended__isnull=False).count()
        if remaining < 0 or not balance_is_known(fill.stock_lot) or unpromised_bulk(fill.stock_lot, fill.source_location) < remaining:
            raise ValidationError({'fill': 'There are not enough empty, unpromised pots to restore this fill.'})
        return
    unit = fill.inventory_unit
    _require_item(unit.item)
    _require_location(fill.workspace, unit.current_location)
    if not unit.active or unit_physical_state(unit) != 'available' or unit_is_in_use(unit):
        raise ValidationError({'fill': 'The container is not empty and available to restore this fill.'})
    if unit.container_fills.filter(sequence__gt=fill.sequence).exists():
        raise ValidationError({'fill': 'The container has been filled again; its earlier fill cannot be restored.'})
    if unit.standing_plants.filter(started__gte=fill.closed_at).exclude(container_fill=fill).exists():
        raise ValidationError({'fill': 'The container has held plants since this clean; its earlier fill cannot be restored.'})
    if SalesOrderAllocation.objects.filter(inventory_unit=unit, status=SalesOrderAllocation.Status.RESERVED).exists():
        raise ValidationError({'fill': 'The container is reserved for an order.'})


@transaction.atomic
def reopen_pot_fill(workspace, user, fill, reason):
    """Correct a clean atomically, retaining its dispositions and departure shares.

    Reclaim the container stock first, then reverse only uncorrected media
    recoveries under media lot locks. Correction rows also retire waste, which
    has no stock movement to reverse. A later clean writes new residuals.
    """
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})
    fill = lock_pot_fills(workspace, [fill.pk])[0]
    if fill.status != SeedTrayGeneration.Status.CLOSED:
        raise ValidationError({'fill': 'Only a closed pot fill can be reopened.'})
    if fill.review_state != SeedTrayGeneration.ReviewState.NONE:
        raise ValidationError({'fill': 'Review this fill before reopening it.'})
    _require_reclaimable_containers(fill)
    residuals = list(fill.residuals.filter(pot_correction__isnull=True).select_related('movement'))
    lock_lots(workspace, [row.lot_id for row in residuals])
    reverse_tray_generation_movements(workspace, [row.movement for row in residuals if row.movement_id], user, reason.strip())
    occurred_at = timezone.now()
    event = SeedTrayGenerationEvent.objects.create(
        generation=fill, event_type=SeedTrayGenerationEvent.EventType.REOPENED,
        occurred_at=occurred_at, reason=reason.strip(),
        created_by=user if user is not None and user.is_authenticated else None,
    )
    for residual in residuals:
        PotFillResidualCorrection.objects.create(residual=residual, event=event)
    SeedTrayGeneration.objects.filter(pk=fill.pk).update(
        status=SeedTrayGeneration.Status.OPEN, closed_at=None,
        close_reason='', closed_by=None, updated=occurred_at,
    )
    fill.refresh_from_db()
    return fill
