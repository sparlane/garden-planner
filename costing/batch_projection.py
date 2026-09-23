"""Read held media separately from the committed production-cost ledger."""

from decimal import Decimal

from applications.models import InputApplication, InputApplicationLine
from inventory.ledger import quantize_money
from plantings.models import SpecificPlantLocation

from .sources import pot_media_line_parts, pot_media_participation


def _pending_media(batch):
    """Reserve the same slots as departure posting, including other batches."""
    placements = SpecificPlantLocation.objects.filter(
        specific_plant__batch=batch, ended__isnull=True,
        container_fill__isnull=False, container_fill__tray__isnull=True,
    ).select_related('container_fill')
    fills = {placement.container_fill_id: placement.container_fill for placement in placements}
    totals, unknown_codes, incomplete_codes = {}, set(), set()
    for fill in fills.values():
        participants = list(fill.plant_locations.select_related('specific_plant').order_by('pk'))
        count, incomplete = pot_media_participation(fill, participants)
        indexes = [index for index, row in enumerate(participants)
                   if row.ended is None and row.specific_plant.batch_id == batch.pk]
        lines = InputApplicationLine.objects.filter(
            application__status=InputApplication.Status.POSTED, targets__container_fill=fill,
        ).select_related('lot').order_by('pk')
        for line in lines:
            code = line.lot.currency_code
            totals.setdefault(code, Decimal('0'))
            if line.lot.base_unit_cost is None:
                unknown_codes.add(code)
            if incomplete:
                incomplete_codes.add(code)
            if incomplete or line.lot.base_unit_cost is None:
                continue
            _, parts = pot_media_line_parts(line, fill, len(participants), count)
            totals[code] += sum((parts[index] for index in indexes), Decimal('0'))
    return totals, unknown_codes, incomplete_codes


def batch_cost_projection(batch, committed):
    """Add held media to posted costs without charging a pot that is still owned.

    Unknown and incomplete currencies retain their known subtotal for audit,
    but cannot provide a projected total. Reading never posts ledger entries.
    """
    pending, unknown, incomplete = _pending_media(batch)
    held = {row['currency_code']: Decimal(row['amount']) for row in committed['currencies']}
    unknown.update(row['currency_code'] for row in committed['layers'] if row['amount'] is None)
    codes = sorted(set(held) | set(pending) | unknown | incomplete)
    rows = []
    for code in codes:
        posted = held.get(code, Decimal('0'))
        media = pending.get(code, Decimal('0'))
        rows.append({
            'currency_code': code,
            'committed_subtotal': f'{quantize_money(posted):f}',
            'pending_media_subtotal': f'{quantize_money(media):f}',
            'projected_total': None if code in unknown | incomplete else f'{quantize_money(posted + media):f}',
            'unknown_cost': code in unknown,
            'not_yet_allocatable': code in incomplete,
        })
    blocked = bool(unknown or incomplete)
    total = None if blocked or len(codes) > 1 else (
        rows[0]['projected_total'] if rows else '0.0000'
    )
    return {
        'currencies': rows, 'projected_total': total,
        'currency_code': codes[0] if len(codes) == 1 else (None if codes else batch.workspace.currency_code),
        'mixed_currency': len(codes) > 1, 'unknown_cost': bool(unknown),
        'not_yet_allocatable': bool(incomplete),
    }
