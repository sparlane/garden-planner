"""Opening, cleaning, and correcting one fill of a seed tray.

A generation is the cultivation cycle a tray's cells are currently serving.
Opening one says the tray has been filled, and it is what a sowing joins and
what an application's cell target is attributed to. Only one is ever open per
tray, which is why nothing here has to ask the caller which fill they meant.

Cleaning is where the care goes. It writes nothing until every remaining plant,
every seed drawn but never sown, and every quantity of media applied has an
explicit disposition, because the alternative is a workflow that quietly decides
seedlings failed and media was thrown away. Nothing is deleted either: sowings
leave the tray's normal view by deriving that view from the generation's status,
so the archive is a filter rather than a loss.

A generation migrated from records that predate the feature is flagged for
review, because those records genuinely cannot say whether the sowings grouped
under it were one fill. Reviewing it is an operator's statement, not an
inference, so it is recorded as its own fact.
"""

# pylint: disable=duplicate-code

import hashlib
from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.ledger import (
    MovementRequest,
    lock_lots,
    post_stock_movement,
    quantize_quantity,
    reverse_tray_generation_movements,
)
from inventory.models import StockMovement

from .models import (
    SeedTrayCell,
    SeedTrayGeneration,
    SeedTrayGenerationEvent,
    SeedTrayGenerationResidual,
)


EventType = SeedTrayGenerationEvent.EventType
Kind = SeedTrayGenerationResidual.Kind
Disposition = SeedTrayGenerationResidual.Disposition

#: Outcomes a clean may record for a plant still sitting in the tray. Everything
#: here resolves the plant; `retained` resolves its availability while leaving it
#: alive, which is what an operator picks for a seedling they are keeping.
CLEAN_OUTCOMES = ('retained', 'failed', 'culled', 'donated')


class PlantDisposition(NamedTuple):
    """What an operator decided about one plant still in the tray."""

    plant_id: int
    outcome: str
    reason: str = ''


class SeedDisposition(NamedTuple):
    """What became of the seed one sowing drew but never placed in a cell."""

    sowing_id: int
    quantity: object
    disposition: str
    reason: str = ''
    destination: object = None


class MediaDisposition(NamedTuple):
    """What became of the media one applied line left in the tray."""

    lot_id: int
    quantity: object
    disposition: str
    reason: str = ''
    destination: object = None


class CloseRequest(NamedTuple):
    """Caller intent for one whole clean."""

    reason: str
    occurred_at: object = None
    plants: tuple = ()
    seeds: tuple = ()
    media: tuple = ()
    digest: object = None
    open_next: bool = False


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def open_generation_for(tray):
    """Return the fill this tray is currently using, or None when it is empty."""
    return SeedTrayGeneration.objects.filter(
        tray=tray,
        status=SeedTrayGeneration.Status.OPEN,
    ).first()


def require_open_generation(tray, field='generation'):
    """Return this tray's open fill, refusing to guess when there is none."""
    generation = open_generation_for(tray)
    if generation is None:
        raise ValidationError({
            field: (
                f'Tray {tray.pk} has no open generation. Fill the tray before '
                'sowing into it or applying an input to its cells.'
            ),
        })
    return generation


def lock_generation(generation):
    """Reload one generation under a row lock, serialising its transitions.

    ``of=('self',)`` is load-bearing, for the reason
    `applications.services.post_application` records. Selecting the tray and the
    workspace alongside builds joins, and an unqualified lock would take those
    rows too — including the workspace row that every plant fact takes a
    key-share lock on. A concurrent outcome would then wait on this transaction
    while this one waited on its plant, which is a deadlock rather than a queue.
    """
    return SeedTrayGeneration.objects.select_for_update(of=('self',)).select_related(
        'tray',
        'workspace',
    ).get(pk=generation.pk)


def _record_event(generation, user, event_type, occurred_at, reason=''):
    """Append one immutable fact about this generation."""
    return SeedTrayGenerationEvent.objects.create(
        generation=generation,
        event_type=event_type,
        occurred_at=occurred_at,
        reason=reason,
        created_by=user if user is not None and user.is_authenticated else None,
    )


@transaction.atomic
def open_generation(tray, user, opened_at=None, notes=''):
    """Record that this tray has been filled and is ready to sow into."""
    existing = SeedTrayGeneration.objects.select_for_update().filter(
        tray=tray,
    ).order_by('-sequence')
    rows = list(existing)
    current = next(
        (row for row in rows if row.status == SeedTrayGeneration.Status.OPEN),
        None,
    )
    if current is not None:
        raise ValidationError({
            'tray': (
                f'Generation {current.code} is still open. Clean the tray before '
                'filling it again.'
            ),
        })
    sequence = (rows[0].sequence + 1) if rows else 1
    opened_at = opened_at or timezone.now()
    generation = SeedTrayGeneration(
        workspace=tray.workspace,
        tray=tray,
        code=f'TRAY-{tray.pk}-{sequence}',
        sequence=sequence,
        opened_at=opened_at,
        notes=notes,
        created_by=user if user is not None and user.is_authenticated else None,
    )
    generation.save()
    _record_event(generation, user, EventType.OPENED, opened_at, 'Tray filled.')
    return generation


@transaction.atomic
def review_generation(generation, user, reason):
    """Confirm a migrated fill really is one fill, unblocking its clean."""
    _require_reason(reason)
    generation = lock_generation(generation)
    if generation.review_state != SeedTrayGeneration.ReviewState.NEEDS_REVIEW:
        raise ValidationError({
            'review_state': 'This generation has already been reviewed.',
        })
    occurred_at = timezone.now()
    SeedTrayGeneration.objects.filter(pk=generation.pk).update(
        review_state=SeedTrayGeneration.ReviewState.NONE,
        updated=occurred_at,
    )
    _record_event(generation, user, EventType.REVIEWED, occurred_at, reason)
    generation.refresh_from_db()
    return generation


def generation_cells(generation):
    """Return every cell of the tray this fill occupies."""
    return SeedTrayCell.objects.filter(
        tray_id=generation.tray_id,
    ).order_by('y_position', 'x_position')


def generation_sowings(generation):
    """Return the sowings made into this fill."""
    from plantings.models import SeedTrayPlanting  # pylint: disable=import-outside-toplevel

    return SeedTrayPlanting.objects.filter(
        generation=generation,
    ).select_related('seeds_used__stock_lot__item', 'batch').order_by('planted', 'pk')


def generation_plants(generation):
    """Return the plants this fill raised that are still sitting in the tray.

    Physical presence is the test rather than lineage. A seedling planted out
    into a garden square already left, and holding up the clean for it would ask
    an operator to dispose of something that is not in the tray.
    """
    from plantings.models import SpecificPlant  # pylint: disable=import-outside-toplevel

    return SpecificPlant.objects.filter(
        cell_planting__seed_tray_planting__generation=generation,
        locations__ended__isnull=True,
        locations__seed_tray_cell__tray_id=generation.tray_id,
    ).select_related('cell_planting').prefetch_related('lifecycle_events').distinct().order_by('pk')


def unresolved_plants(generation):
    """Return the plants in the tray that have recorded no final outcome."""
    from plantings.lifecycle import is_final, plant_lifecycle_summary  # pylint: disable=import-outside-toplevel

    return [
        plant for plant in generation_plants(generation)
        if not is_final(plant_lifecycle_summary(plant).state)
    ]


def unsown_seed(sowing):
    """Return the seed this sowing drew from the packet but never placed.

    Seed sown into a cell that never came up is a plant outcome, not loose seed,
    so only the unallocated remainder is counted here.
    """
    allocated = sowing.cell_plantings.aggregate(total=Sum('quantity'))['total'] or 0
    return max(sowing.quantity - allocated, 0)


def applied_media(generation):
    """Return every posted application line that put media into this fill.

    A reversed application put its stock back, so it left nothing in the tray
    and nothing here to dispose of.
    """
    from applications.models import InputApplication, InputApplicationLine  # pylint: disable=import-outside-toplevel

    return InputApplicationLine.objects.filter(
        application__status=InputApplication.Status.POSTED,
        targets__seed_tray_generation=generation,
    ).select_related(
        'item',
        'lot',
        'application',
    ).prefetch_related('targets').distinct().order_by('pk')


def cell_shares(line):
    """Return each cell target of one line paired with its share of it.

    A line can spread over two trays, so its cells are weighted the same way
    ``applications.usage`` weighted them when it calculated the quantity: by
    ``weight * cell_volume_ml``. The basis spans the whole line, including cells
    belonging to another fill, because that is how the quantity was arrived at;
    splitting any other way would report a number the application never used.
    """
    targets = [row for row in line.targets.all() if row.cell_volume_ml]
    basis = sum(
        Decimal(row.weight) * Decimal(row.cell_volume_ml)
        for row in targets
    )
    if not basis:
        return []
    return [
        (row, (Decimal(row.weight) * Decimal(row.cell_volume_ml)) / basis)
        for row in targets
    ]


def _media_rows(generation):
    """Total what each lot contributed to this fill, at its recorded cost."""
    totals = {}
    for line in applied_media(generation):
        share = sum(
            (
                Decimal(line.applied_base_quantity) * portion
                for target, portion in cell_shares(line)
                if target.seed_tray_generation_id == generation.pk
            ),
            Decimal('0'),
        )
        if not share:
            continue
        row = totals.setdefault(line.lot_id, {
            'lot': line.lot,
            'item': line.item,
            'base_unit': line.base_unit,
            'unit_cost': line.lot.base_unit_cost,
            'base_quantity': Decimal('0'),
        })
        row['base_quantity'] += share
    return [
        {**row, 'base_quantity': quantize_quantity(row['base_quantity'])}
        for row in totals.values()
    ]


def generation_contents(generation):
    """Describe everything a clean has to find a disposition for."""
    sowings = list(generation_sowings(generation))
    seeds = [
        {'sowing': sowing, 'quantity': unsown_seed(sowing)}
        for sowing in sowings
    ]
    return {
        'generation': generation,
        'cell_count': generation_cells(generation).count(),
        'sowings': sowings,
        'plants': unresolved_plants(generation),
        'seeds': [row for row in seeds if row['quantity'] > 0],
        'media': _media_rows(generation),
    }


def contents_digest(contents):
    """Summarize what a confirmation screen showed, so a stale one is refused.

    Formatted like ``applications.services.availability_digest`` so both halves
    of this codebase describe drift the same way. Anything that would change
    what the operator has to decide about is in here.
    """
    rows = [f'plant:{plant.pk}' for plant in contents['plants']]
    rows.extend(
        f'seed:{row["sowing"].pk}:{row["quantity"]}'
        for row in contents['seeds']
    )
    rows.extend(
        f'media:{row["lot"].pk}:{quantize_quantity(row["base_quantity"]):.9f}'
        for row in contents['media']
    )
    canonical = '\n'.join(sorted(rows))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _require_current(contents, digest):
    """Refuse a confirmation submitted against contents that have since moved."""
    if digest is not None and contents_digest(contents) != digest:
        raise ValidationError({
            'digest': (
                'The tray changed after this clean was prepared. Review it again.'
            ),
        })


def _require_cleanable(generation):
    """Refuse to clean a fill that is closed or still awaiting review."""
    if generation.status != SeedTrayGeneration.Status.OPEN:
        raise ValidationError({
            'status': f'Generation {generation.code} is already closed.',
        })
    if generation.review_state == SeedTrayGeneration.ReviewState.NEEDS_REVIEW:
        raise ValidationError({
            'review_state': (
                f'Generation {generation.code} was migrated from earlier records '
                'and must be reviewed before the tray can be cleaned.'
            ),
        })


def _match_plants(contents, dispositions):
    """Pair every plant in the tray with the outcome an operator chose."""
    wanted = {plant.pk: plant for plant in contents['plants']}
    chosen = {}
    for row in dispositions:
        if row.plant_id not in wanted:
            raise ValidationError({
                'plants': f'Plant {row.plant_id} is not in this generation.',
            })
        if row.outcome not in CLEAN_OUTCOMES:
            raise ValidationError({
                'plants': (
                    f'Plant {row.plant_id}: record one of '
                    f'{", ".join(CLEAN_OUTCOMES)}.'
                ),
            })
        chosen[row.plant_id] = row
    missing = sorted(set(wanted) - set(chosen))
    if missing:
        raise ValidationError({
            'plants': f'These plants still need an outcome: {missing}.',
        })
    return [(wanted[plant_id], chosen[plant_id]) for plant_id in sorted(chosen)]


def _match_quantities(expected, dispositions, key, field, allowed):
    """Pair every leftover quantity with the disposition an operator recorded.

    Both halves are checked. An unlisted leftover would be silently assumed away,
    and a disposition for more than was left would put stock back that never
    existed.
    """
    chosen = {}
    for row in dispositions:
        identifier = getattr(row, key)
        if identifier not in expected:
            raise ValidationError({
                field: f'{identifier} has nothing left over in this generation.',
            })
        if row.disposition not in allowed:
            raise ValidationError({
                field: f'{identifier}: record one of {", ".join(allowed)}.',
            })
        quantity = quantize_quantity(row.quantity)
        if quantity <= 0:
            raise ValidationError({
                field: f'{identifier}: record a quantity greater than zero.',
            })
        chosen[identifier] = chosen.get(identifier, Decimal('0')) + quantity
        if chosen[identifier] > quantize_quantity(expected[identifier]):
            raise ValidationError({
                field: (
                    f'{identifier}: {chosen[identifier]} is more than the '
                    f'{quantize_quantity(expected[identifier])} left over.'
                ),
            })
    for identifier, quantity in sorted(expected.items()):
        if chosen.get(identifier, Decimal('0')) != quantize_quantity(quantity):
            raise ValidationError({
                field: (
                    f'{identifier}: account for all '
                    f'{quantize_quantity(quantity)} left over.'
                ),
            })


def _resolve_plants(pairs, user, occurred_at):
    """Record each chosen outcome, then empty the cell the plant occupied."""
    from plantings.lifecycle import OutcomeRequest, record_lifecycle_event  # pylint: disable=import-outside-toplevel
    from plantings.models import SpecificPlant, SpecificPlantLocation  # pylint: disable=import-outside-toplevel

    # Every plant is locked up front in primary-key order, before any of them is
    # written, so two cleans of overlapping selections queue rather than each
    # holding half of what the other needs.
    list(
        SpecificPlant.objects
        .select_for_update()
        .filter(pk__in=[plant.pk for plant, _ in pairs])
        .order_by('pk')
    )
    events = []
    for plant, chosen in pairs:
        events.append(record_lifecycle_event(plant, user, OutcomeRequest(
            event_type=chosen.outcome,
            occurred_at=occurred_at,
            reason=chosen.reason,
        )))
        # A retained plant keeps growing, so its outcome does not close a
        # location. The tray is being emptied all the same, so the cell it was
        # sitting in has to stop being where it is.
        SpecificPlantLocation.objects.filter(
            specific_plant=plant,
            ended__isnull=True,
        ).update(ended=occurred_at)
    return events


def _recover_stock(generation, user, values, occurred_at):
    """Put one recovered remainder back into stock as an adjustment gain."""
    if values['destination'] is None:
        raise ValidationError({
            'destination': 'Name where the recovered stock was put.',
        })
    return post_stock_movement(
        generation.workspace,
        user,
        MovementRequest(
            lot=values['lot'],
            movement_type=StockMovement.MovementType.ADJUSTMENT_GAIN,
            quantity=values['quantity'],
            destination=values['destination'],
            occurred_at=occurred_at,
            reason=values['reason'],
            reference=f'tray generation:{generation.pk}',
        ),
    )


def _write_residual(generation, user, values, occurred_at):
    """Record one disposition, moving stock only when something came back."""
    movement = None
    if values['disposition'] in SeedTrayGenerationResidual.RECOVERING:
        movement = _recover_stock(generation, user, values, occurred_at)
    return SeedTrayGenerationResidual.objects.create(
        generation=generation,
        kind=values['kind'],
        disposition=values['disposition'],
        lot=values['lot'],
        sowing=values.get('sowing'),
        base_quantity=values['quantity'],
        base_unit=values['lot'].item.base_unit,
        unit_cost=values['lot'].base_unit_cost,
        movement=movement,
        reason=values['reason'],
        created_by=user if user is not None and user.is_authenticated else None,
    )


def _seed_lot(sowing):
    """Return the lot a sowing's leftover seed goes back to, or refuse."""
    lot = sowing.seeds_used.stock_lot
    if lot is None:
        raise ValidationError({
            'seeds': (
                f'Sowing {sowing.pk} draws on a packet with no stock lot, so its '
                'leftover seed cannot be recorded.'
            ),
        })
    return lot


@transaction.atomic
def close_generation(generation, user, request):  # pylint: disable=too-many-locals
    """Empty the tray, resolving everything left in it and closing the fill.

    Locks are taken as generation, then plants, then batches, then lots.
    ``applications.services.post_application`` documents why the last three run
    in that order; the generation is a new outermost level that nothing else
    takes, so extending the chain rather than starting a new one keeps this
    compatible with sowing, harvesting, and posting an application.

    Repeating a submission is refused rather than half-applied: the status check
    runs under the lock, and everything happens in one transaction.
    """
    _require_reason(request.reason)
    generation = lock_generation(generation)
    _require_cleanable(generation)
    occurred_at = request.occurred_at or timezone.now()

    contents = generation_contents(generation)
    _require_current(contents, request.digest)

    plant_pairs = _match_plants(contents, request.plants)
    seed_totals = {
        row['sowing'].pk: Decimal(row['quantity'])
        for row in contents['seeds']
    }
    media_totals = {
        row['lot'].pk: Decimal(row['base_quantity'])
        for row in contents['media']
    }
    _match_quantities(
        seed_totals,
        request.seeds,
        'sowing_id',
        'seeds',
        (Disposition.REMOVED, Disposition.RETURNED),
    )
    _match_quantities(
        media_totals,
        request.media,
        'lot_id',
        'media',
        (Disposition.WASTE, Disposition.RECLAIMED),
    )

    _resolve_plants(plant_pairs, user, occurred_at)

    sowings = {sowing.pk: sowing for sowing in contents['sowings']}
    lots = {row['lot'].pk: row['lot'] for row in contents['media']}
    lock_lots(generation.workspace, sorted(lots))
    for row in request.seeds:
        sowing = sowings[row.sowing_id]
        _write_residual(generation, user, {
            'kind': Kind.SEED,
            'disposition': row.disposition,
            'lot': _seed_lot(sowing),
            'sowing': sowing,
            'quantity': quantize_quantity(row.quantity),
            'destination': row.destination or sowing.seeds_used.storage_location,
            'reason': row.reason,
        }, occurred_at)
    for row in request.media:
        _write_residual(generation, user, {
            'kind': Kind.MEDIA,
            'disposition': row.disposition,
            'lot': lots[row.lot_id],
            'quantity': quantize_quantity(row.quantity),
            'destination': row.destination,
            'reason': row.reason,
        }, occurred_at)

    SeedTrayGeneration.objects.filter(pk=generation.pk).update(
        status=SeedTrayGeneration.Status.CLOSED,
        closed_at=occurred_at,
        close_reason=request.reason.strip(),
        closed_by=user if user is not None and user.is_authenticated else None,
        updated=occurred_at,
    )
    _record_event(generation, user, EventType.CLOSED, occurred_at, request.reason)
    generation.refresh_from_db()

    following = None
    if request.open_next:
        following = open_generation(generation.tray, user, occurred_at)
    return generation, following


@transaction.atomic
def reopen_generation(generation, user, reason):
    """Undo a clean that should not have happened, without erasing it.

    Everything the clean wrote stays: the closing event keeps its time and its
    stated reason, each residual keeps its quantity, and every lifecycle outcome
    keeps its row. What is appended is the correction — a reversal per recovered
    movement, a correction per outcome, and the reopening itself.

    A closed location is deliberately not reopened. Where a plant has been
    remains true, and `plantings.lifecycle` records a replacement location
    rather than rewinding one.
    """
    _require_reason(reason)
    generation = lock_generation(generation)
    if generation.status != SeedTrayGeneration.Status.CLOSED:
        raise ValidationError({'status': 'Only a closed generation can be reopened.'})
    if open_generation_for(generation.tray) is not None:
        raise ValidationError({
            'tray': (
                'The tray has been filled again. Clean the current generation '
                'before correcting the previous one.'
            ),
        })
    occurred_at = timezone.now()
    _reverse_recovered_stock(generation, user, reason)
    _reverse_close_outcomes(generation, user, reason, occurred_at)
    SeedTrayGeneration.objects.filter(pk=generation.pk).update(
        status=SeedTrayGeneration.Status.OPEN,
        closed_at=None,
        close_reason='',
        closed_by=None,
        updated=occurred_at,
    )
    _record_event(generation, user, EventType.REOPENED, occurred_at, reason)
    generation.refresh_from_db()
    return generation


def _reverse_recovered_stock(generation, user, reason):
    """Take back every quantity this clean returned to the shelf."""
    movements = list(
        StockMovement.objects.select_for_update(of=('self',))
        .select_related('lot__item', 'workspace', 'source', 'destination')
        .filter(tray_generation_residual__generation=generation)
        .order_by('pk')
    )
    if not movements:
        return []
    return reverse_tray_generation_movements(
        generation.workspace,
        movements,
        user,
        reason,
    )


def _reverse_close_outcomes(generation, user, reason, occurred_at):
    """Correct each outcome this clean recorded, leaving the mistake visible."""
    from plantings.lifecycle import reverse_lifecycle_event  # pylint: disable=import-outside-toplevel
    from plantings.models import PlantLifecycleEvent  # pylint: disable=import-outside-toplevel

    events = list(
        PlantLifecycleEvent.objects
        .filter(
            plant__cell_planting__seed_tray_planting__generation=generation,
            occurred_at=generation.closed_at,
            event_type__in=CLEAN_OUTCOMES,
            reversal__isnull=True,
        )
        .select_related('plant')
        .order_by('pk')
    )
    return [
        reverse_lifecycle_event(event, user, reason, occurred_at)
        for event in events
    ]
