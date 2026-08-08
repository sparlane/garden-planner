"""Transactional services for drafting, posting, and reversing applications.

A draft is assembled and checked against stock; posting is what decrements
inventory. The two are separate because a calculation only ever proposes a
quantity, and an operator confirms what was actually used, often well after
the draft was built.

Posting revalidates everything against the state it holds locks on, and refuses
a document whose stock or contents moved since the client last looked. That is
what stops two people spending the same last litre of a lot.
"""

# pylint: disable=duplicate-code

import hashlib
from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from garden.geometry import square_metres
from inventory.ledger import (
    MovementRequest,
    lock_lots,
    normalize_quantity,
    physical_balance,
    post_stock_movement,
    quantize_quantity,
    reverse_application_movements,
)
from inventory.models import InventoryItem, StockMovement
from plantings.batches import batch_specific_plants, lock_batch
from plantings.lifecycle import is_final, lifecycle_summaries
from plantings.models import ProductionBatch, SpecificPlant
from seedtrays.generations import require_open_generation
from seedtrays.models import SeedTrayCell, SeedTrayGeneration

from .models import InputApplication, InputApplicationLine, InputApplicationTarget
from .usage import TargetInput, UsageInputs, calculate_usage, workspace_override_required


#: Batch states that can still receive an input. `planned` has grown nothing
#: yet and `cancelled` has declared it produced nothing at all, so neither can
#: truthfully consume stock.
APPLICABLE_STATUSES = {
    ProductionBatch.Status.ACTIVE,
    ProductionBatch.Status.OUTPUT_FINALIZED,
    ProductionBatch.Status.COMPLETED,
}

TargetType = InputApplicationTarget.TargetType


class TargetRequest(NamedTuple):
    """Caller intent for one thing an input was applied to."""

    target_type: str
    target: object
    weight: object = Decimal('1')


class LineRequest(NamedTuple):
    """Caller intent for one item drawn from one exact lot."""

    item: object
    lot: object
    applied_quantity: object
    unit_code: object = None
    unit_conversion: object = None
    usage_basis: str = ''
    fill_factor: object = None
    waste_quantity: object = Decimal('0')
    waste_reason: str = ''
    override_reason: str = ''
    notes: str = ''
    targets: tuple = ()


class ApplicationRequest(NamedTuple):
    """Caller intent for one whole document."""

    applied_at: object
    source_location: object
    batch: object = None
    notes: str = ''
    lines: tuple = ()


class Posting(NamedTuple):
    """One ledger row a line is about to write: what, how much, and why.

    A line posts consumption and, when there is any, waste. Both draw on the
    same lot from the same location at the same moment, so only these three
    differ between them.
    """

    movement_type: str
    quantity: object
    reason: str = ''


class TargetSnapshot(NamedTuple):
    """One target with whatever it measured at the moment it was read."""

    target_type: str
    target: object = None
    weight: object = Decimal('1')
    cell_volume_ml: object = None
    area_m2: object = None
    label: str = ''
    seed_tray_generation: object = None

    def as_usage_input(self):
        """Return the pure-calculation view of this target."""
        return TargetInput(
            target_type=self.target_type,
            weight=Decimal(self.weight),
            cell_volume_ml=self.cell_volume_ml,
            area_m2=self.area_m2,
            label=self.label,
        )


def _require_reason(reason):
    """Reject an audit-critical action without a stated reason."""
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'A reason is required.'})


def measure_target(request):
    """Freeze what one target measures right now.

    Reading happens once, here, so everything downstream works from the frozen
    copy and a later edit to a tray model or an area's confirmed scale cannot
    change what an application already recorded.
    """
    target = request.target
    snapshot = TargetSnapshot(
        target_type=request.target_type,
        target=target,
        weight=Decimal(request.weight),
        label=str(target)[:255],
    )
    if request.target_type == TargetType.SEED_TRAY_CELL:
        volume = target.tray.model.cell_size_ml
        if not volume:
            raise ValidationError({
                'targets': f'Tray model {target.tray.model} has no recorded cell volume.',
            })
        # The cell says where this went; the generation says which crop was
        # using it. Both are frozen here, because a tray cleaned afterwards
        # would otherwise leave this document pointing at the next crop's fill.
        return snapshot._replace(
            cell_volume_ml=volume,
            seed_tray_generation=require_open_generation(target.tray, field='targets'),
        )
    if request.target_type in {
        TargetType.GARDEN_AREA,
        TargetType.GARDEN_BED,
        TargetType.GARDEN_ROW,
        TargetType.GARDEN_SQUARE,
    }:
        try:
            return snapshot._replace(area_m2=square_metres(target))
        except ValidationError as exc:
            # Re-key onto this app's field so every target problem an operator
            # can hit arrives under `targets`, whichever layer noticed it.
            raise ValidationError({'targets': exc.messages}) from exc
    return snapshot


def stored_snapshot(row):
    """Rebuild a snapshot from a saved target row without remeasuring it."""
    return TargetSnapshot(
        target_type=row.target_type,
        target=row.target,
        weight=row.weight,
        cell_volume_ml=row.cell_volume_ml,
        area_m2=row.area_m2,
        label=row.label,
        seed_tray_generation=row.seed_tray_generation,
    )


def _line_usage(item, basis, fill_factor, snapshots):
    """Calculate one line's suggestion from its snapshotted targets."""
    return calculate_usage(UsageInputs(
        basis=basis or item.default_usage_basis,
        base_unit=item.base_unit,
        rate=item.default_usage_rate,
        rate_unit=item.usage_rate_unit or '',
        fixed_quantity=item.default_fixed_quantity,
        fill_factor=fill_factor,
        targets=tuple(snapshot.as_usage_input() for snapshot in snapshots),
    ))


def _stored_line_usage(line):
    """Recalculate one saved line entirely from its own frozen columns.

    Nothing here reads the catalog. That is the whole point: a posted document
    must produce the same number after an item's rate has been edited.
    """
    return calculate_usage(UsageInputs(
        basis=line.usage_basis,
        base_unit=line.base_unit,
        rate=line.configured_rate,
        rate_unit=line.configured_rate_unit,
        fixed_quantity=line.configured_fixed_quantity,
        fill_factor=line.fill_factor,
        targets=tuple(
            stored_snapshot(row).as_usage_input() for row in line.targets.all()
        ),
    ))


def _validate_batch(batch, applied_at):
    """Require a batch that can still truthfully consume stock."""
    if batch is None:
        return
    if batch.status not in APPLICABLE_STATUSES:
        raise ValidationError({
            'batch': (
                f'A {batch.get_status_display().lower()} batch cannot receive '
                'an input application.'
            ),
        })
    if batch.actual_start is not None and applied_at < batch.actual_start:
        raise ValidationError({
            'applied_at': 'An application cannot predate the start of its batch.',
        })


def _plant_ids(application):
    """Return every plant this document targets, in primary-key order."""
    return sorted(
        InputApplicationTarget.objects
        .filter(line__application=application, specific_plant__isnull=False)
        .values_list('specific_plant_id', flat=True)
    )


def _validate_plants(application, plant_ids):
    """Require living plants that came from the document's own batch."""
    if not plant_ids:
        return
    summaries = lifecycle_summaries(plant_ids)
    finished = sorted(
        plant_id for plant_id, summary in summaries.items()
        if is_final(summary.state)
    )
    if finished:
        raise ValidationError({
            'targets': f'These plants had already finished: {finished}.',
        })
    if application.batch_id is None:
        return
    known = set(
        batch_specific_plants(application.batch)
        .filter(pk__in=plant_ids)
        .values_list('pk', flat=True)
    )
    missing = [plant_id for plant_id in plant_ids if plant_id not in known]
    if missing:
        raise ValidationError({
            'targets': (
                f'These plants did not come from batch '
                f'{application.batch.code}: {missing}.'
            ),
        })


def _validate_generations(application):
    """Refuse a draft whose tray was cleaned while it sat unposted.

    The generation was frozen when the draft was built. Posting against a fill
    that has since been emptied would charge this media to a crop that is no
    longer in the tray, so the document has to be rebuilt against the new fill.
    """
    closed = sorted(
        SeedTrayGeneration.objects.filter(
            application_targets__line__application=application,
            status=SeedTrayGeneration.Status.CLOSED,
        ).values_list('code', flat=True).distinct()
    )
    if closed:
        raise ValidationError({
            'targets': (
                f'These tray generations have been cleaned since this draft was '
                f'built: {", ".join(closed)}. Rebuild it against the current fill.'
            ),
        })


def _validate_line_amounts(workspace, line, calculation):
    """Require waste and override reasons wherever the audit needs them."""
    if line.waste_base_quantity > 0 and not line.waste_reason.strip():
        raise ValidationError({
            'lines': f'Line {line.pk}: a reason is required for recorded waste.',
        })
    required = workspace_override_required(
        workspace,
        calculation.calculated_base_quantity,
        line.applied_base_quantity,
    )
    if required and not line.override_reason.strip():
        raise ValidationError({
            'lines': (
                f'Line {line.pk}: give a reason for applying '
                f'{line.applied_base_quantity} {line.base_unit} instead of the '
                f'calculated {calculation.calculated_base_quantity}.'
            ),
        })


def availability_digest(rows):
    """Summarize the stock a preview reported, so posting can detect drift.

    Each row is `(lot_id, location_id, balance)`. The balance is formatted the
    way the balances endpoint formats it, so a client that read availability
    from either place derives the same digest.
    """
    canonical = '\n'.join(
        f'{lot_id}:{location_id}:{quantize_quantity(balance):.9f}'
        for lot_id, location_id, balance in sorted(rows)
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def application_availability(application):
    """Return the digest inputs for every lot this document draws on."""
    seen = {}
    for line in application.lines.all():
        key = (line.lot_id, application.source_location_id)
        if key not in seen:
            seen[key] = physical_balance(line.lot, application.source_location)
    return [(lot_id, location_id, balance) for (lot_id, location_id), balance in seen.items()]


def application_state(application):
    """Describe a draft: its calculations, its stock, and what may drift.

    This is what a preview returns and what posting revalidates against, so the
    two can never disagree about what the operator was shown.
    """
    workspace = application.workspace
    rows = application_availability(application)
    balances = {(lot_id, location_id): balance for lot_id, location_id, balance in rows}
    drawn = {}
    lines = []
    for line in application.lines.all():
        calculation = _stored_line_usage(line)
        key = (line.lot_id, application.source_location_id)
        available = balances[key]
        drawn[key] = drawn.get(key, Decimal('0')) + line.applied_base_quantity + line.waste_base_quantity
        lines.append({
            'pk': line.pk,
            'item': line.item_id,
            'lot': line.lot_id,
            'usage_basis': line.usage_basis,
            'basis_quantity': calculation.basis_quantity,
            'basis_unit': calculation.basis_unit,
            'target_count': calculation.target_count,
            'formula': calculation.formula,
            'calculated_base_quantity': calculation.calculated_base_quantity,
            'applied_base_quantity': line.applied_base_quantity,
            'waste_base_quantity': line.waste_base_quantity,
            'base_unit': line.base_unit,
            'available_base_quantity': available,
            'available_after_base_quantity': available - drawn[key],
            'override_required': workspace_override_required(
                workspace,
                calculation.calculated_base_quantity,
                line.applied_base_quantity,
            ),
            'short': drawn[key] > available,
        })
    return {
        'revision': application.revision,
        'availability_digest': availability_digest(rows),
        'target_summary': target_summary(application),
        'lines': lines,
    }


def target_summary(application):
    """Render one readable sentence naming what this document went on."""
    counts = {}
    for line in application.lines.all():
        for row in line.targets.all():
            counts[row.target_type] = counts.get(row.target_type, 0) + 1
    if not counts:
        return ''
    labels = dict(TargetType.choices)
    parts = [
        f'{count} {labels[target_type].lower()}{"s" if count != 1 else ""}'
        for target_type, count in sorted(counts.items())
    ]
    return '; '.join(parts)


@transaction.atomic
def create_application_draft(workspace, user, request):
    """Assemble a draft, freezing every measurement it will calculate from."""
    if not request.lines:
        raise ValidationError({'lines': 'Add at least one application line.'})
    application = InputApplication(
        workspace=workspace,
        applied_at=request.applied_at,
        source_location=request.source_location,
        batch=request.batch,
        notes=request.notes,
        created_by=user if user is not None and user.is_authenticated else None,
    )
    application.save()
    for line_request in request.lines:
        _create_line(application, line_request)
    InputApplication.objects.filter(pk=application.pk).update(
        target_summary=target_summary(application),
    )
    application.refresh_from_db()
    return application


@transaction.atomic
def update_application_draft(application, request, replace_lines=True):
    """Edit a draft, bumping the revision a client has to echo back.

    Every change moves the revision, including one that leaves the numbers
    alone, because the point is to invalidate what another tab was shown
    rather than to describe how much changed.
    """
    application = InputApplication.objects.select_for_update().get(pk=application.pk)
    if application.status != InputApplication.Status.DRAFT:
        raise ValidationError({'status': 'Only draft applications can be edited.'})
    application.applied_at = request.applied_at
    application.source_location = request.source_location
    application.batch = request.batch
    application.notes = request.notes
    application.save()
    if replace_lines:
        if not request.lines:
            raise ValidationError({'lines': 'Add at least one application line.'})
        application.lines.all().delete()
        for line_request in request.lines:
            _create_line(application, line_request)
    InputApplication.objects.filter(pk=application.pk).update(
        revision=application.revision + 1,
        target_summary=target_summary(application),
    )
    application.refresh_from_db()
    return application


def _create_line(application, request):
    """Create one line, its frozen catalog snapshot, and its targets."""
    item = request.item
    basis = request.usage_basis or item.default_usage_basis
    snapshots = [measure_target(target) for target in request.targets]
    calculation = _line_usage(item, basis, request.fill_factor, snapshots)
    # The ledger refuses anything but a Decimal or string, to keep a float from
    # ever reaching a quantity column. Convert once here so no caller has to.
    applied_quantity = Decimal(request.applied_quantity)
    waste_quantity = Decimal(request.waste_quantity)
    line = InputApplicationLine(
        application=application,
        item=item,
        lot=request.lot,
        usage_basis=basis,
        base_unit=item.base_unit,
        configured_rate=item.default_usage_rate,
        configured_rate_unit=item.usage_rate_unit or '',
        configured_fixed_quantity=item.default_fixed_quantity,
        fill_factor=request.fill_factor,
        formula_basis_quantity=calculation.basis_quantity,
        formula_basis_unit=calculation.basis_unit,
        calculated_base_quantity=calculation.calculated_base_quantity,
        applied_quantity=applied_quantity,
        unit_code=request.unit_code,
        unit_conversion=request.unit_conversion,
        applied_base_quantity=normalize_quantity(
            item,
            applied_quantity,
            unit_code=request.unit_code,
            unit_conversion=request.unit_conversion,
        ),
        waste_quantity=waste_quantity,
        waste_base_quantity=normalize_quantity(
            item,
            waste_quantity,
            unit_code=request.unit_code,
            unit_conversion=request.unit_conversion,
            allow_zero=True,
        ),
        waste_reason=request.waste_reason,
        override_reason=request.override_reason,
        notes=request.notes,
    )
    line.save()
    for snapshot in snapshots:
        InputApplicationTarget.objects.create(
            line=line,
            target_type=snapshot.target_type,
            weight=snapshot.weight,
            cell_volume_ml=snapshot.cell_volume_ml,
            area_m2=snapshot.area_m2,
            label=snapshot.label,
            seed_tray_generation=snapshot.seed_tray_generation,
            **{snapshot.target_type: snapshot.target},
        )
    return line


@transaction.atomic
def post_application(application, user, revision=None, digest=None):
    """Decrement stock for a confirmed draft under every relevant lock.

    Locks are taken as plants, then the batch, then lots. That order is
    load-bearing: `plantings.harvests.record_harvest` documents and
    `plantings.test_harvest_concurrency` proves that a plant must be locked
    before its batch, because writing a plant fact takes a key-share lock on
    the batch row. Sowing establishes batch before lot. Extending the same
    chain rather than inventing one is what keeps this compatible with both.

    Every lock names `of=('self',)`. The batch is nullable, so selecting it
    alongside builds an outer join, and PostgreSQL refuses to lock across the
    nullable side of one. Only this document's own row needs holding anyway.
    """
    application = InputApplication.objects.select_for_update(of=('self',)).select_related(
        'workspace',
        'batch',
        'source_location',
    ).get(pk=application.pk)
    if application.status != InputApplication.Status.DRAFT:
        raise ValidationError({'status': 'Only draft applications can be posted.'})

    plant_ids = _plant_ids(application)
    if plant_ids:
        list(SpecificPlant.objects.select_for_update().filter(pk__in=plant_ids).order_by('pk'))
    batch = lock_batch(application.batch) if application.batch_id else None
    lines = list(application.lines.select_related('item', 'lot').prefetch_related('targets'))
    if not lines:
        raise ValidationError({'lines': 'Add at least one application line.'})
    lock_lots(application.workspace, [line.lot_id for line in lines])

    _validate_batch(batch, application.applied_at)
    _validate_plants(application, plant_ids)
    _validate_generations(application)
    _require_current(application, lines, revision, digest)

    movements = []
    for line in lines:
        movements.extend(_post_line(application, line, user))
    posted_at = timezone.now()
    InputApplication.objects.filter(pk=application.pk).update(
        status=InputApplication.Status.POSTED,
        posted_at=posted_at,
        target_summary=target_summary(application),
        updated=posted_at,
    )
    application.refresh_from_db()
    return application, movements


def _require_current(application, lines, revision, digest):
    """Refuse a document whose contents or stock moved since the client looked.

    Both halves matter. The revision catches somebody editing the draft in
    another tab, and the digest catches somebody else spending the stock this
    document was about to draw on. Neither is checked until the locks are held,
    so a passing check stays true through to the write.
    """
    if revision is not None and application.revision != revision:
        raise ValidationError({
            'revision': 'This draft changed after the preview. Preview it again.',
        })
    for line in lines:
        calculation = _stored_line_usage(line)
        _validate_line_amounts(application.workspace, line, calculation)
    if digest is not None and availability_digest(application_availability(application)) != digest:
        raise ValidationError({
            'availability_digest': (
                'Stock changed after the preview. Preview it again.'
            ),
        })


def _post_line(application, line, user):
    """Consume one line's confirmed quantity, and its waste when there is any."""
    if line.item.tracking_mode == InventoryItem.TrackingMode.SERIALIZED:
        raise ValidationError({
            'lines': f'Line {line.pk}: serialized stock moves through unit actions.',
        })
    movements = [_post_movement(application, line, user, Posting(
        movement_type=StockMovement.MovementType.CONSUMPTION,
        quantity=line.applied_base_quantity,
        reason=line.override_reason,
    ))]
    line.consumption_movement = movements[0]
    if line.waste_base_quantity > 0:
        movements.append(_post_movement(application, line, user, Posting(
            movement_type=StockMovement.MovementType.WASTE,
            quantity=line.waste_base_quantity,
            reason=line.waste_reason,
        )))
        line.waste_movement = movements[1]
    InputApplicationLine.objects.filter(pk=line.pk).update(
        consumption_movement=line.consumption_movement,
        waste_movement=line.waste_movement,
    )
    line.item.mark_stock_history_started(application.applied_at)
    return movements


def _post_movement(application, line, user, posting):
    """Append one ledger row for this line."""
    return post_stock_movement(
        application.workspace,
        user,
        MovementRequest(
            lot=line.lot,
            movement_type=posting.movement_type,
            quantity=posting.quantity,
            source=application.source_location,
            occurred_at=application.applied_at,
            reason=posting.reason,
            reference=f'application:{application.pk} line:{line.pk}',
        ),
    )


def _posted_movement_filter(application):
    """Match every movement this document's lines posted, of either kind."""
    return Q(application_consumption__application=application) | Q(application_waste__application=application)


@transaction.atomic
def reverse_application(application, user, reason):
    """Put back everything one application took, keeping the document on file.

    The calculation, the targets, and the movements all stay readable. A
    corrected application is a new document, so the mistake and its correction
    are both visible rather than one overwriting the other.
    """
    _require_reason(reason)
    application = InputApplication.objects.select_for_update(of=('self',)).select_related(
        'workspace',
    ).get(pk=application.pk)
    if application.status != InputApplication.Status.POSTED:
        raise ValidationError({'status': 'Only posted applications can be reversed.'})
    movements = list(
        StockMovement.objects.select_for_update(of=('self',))
        .select_related('lot__item', 'workspace', 'source', 'destination')
        .filter(_posted_movement_filter(application))
        .order_by('pk')
    )
    reverse_application_movements(application.workspace, movements, user, reason)
    reversed_at = timezone.now()
    InputApplication.objects.filter(pk=application.pk).update(
        status=InputApplication.Status.REVERSED,
        reversed_at=reversed_at,
        reverse_reason=reason.strip(),
        reversed_by=user if user is not None and user.is_authenticated else None,
        updated=reversed_at,
    )
    application.refresh_from_db()
    return application


def application_targets(application):
    """Return every target row this document recorded, newest line first."""
    return InputApplicationTarget.objects.filter(
        line__application=application,
    ).select_related('line')


def cells_for_tray(tray):
    """Return one target request per cell of a tray, for the whole-tray path.

    The shortcut still stores individual cells, so a document always names
    exactly which ones were filled even when the operator picked the tray.
    """
    return [
        TargetRequest(target_type=TargetType.SEED_TRAY_CELL, target=cell)
        for cell in SeedTrayCell.objects.filter(tray=tray).order_by('y_position', 'x_position')
    ]
