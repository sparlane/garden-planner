"""Settle the cost of sale of lines posted before their batch was finalized."""

from django.core.management.base import BaseCommand

from costing.services import is_frozen
from plantings.models import ProductionBatch
from sales.models import FulfillmentLineSettlement
from sales.settlement import provisional_plant_lines, settle_batch_sales, settlement_of
from workspaces.models import get_current_workspace


class Command(BaseCommand):
    """Settle every finalized batch's provisional plant lines, as a recorded run.

    Finalizing a batch settles its sales from then on. This is for the ones
    posted and finalized before that existed. Each settlement is a row naming
    this reason, so the money it writes is auditable and not a silent
    migration. Safe to run again: a line that is already settled is skipped.
    """

    help = (
        'Settle provisional cost of sale for plants sold before their batch '
        'was finalized. Use --dry-run to list what would be settled.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', dest='dry_run')
        parser.add_argument(
            '--reason', default='Settled retrospectively (task 139).',
            help='Reason recorded against each settlement.',
        )

    def handle(self, *args, **options):
        workspace = get_current_workspace()
        batches = [
            batch for batch in ProductionBatch.objects.filter(workspace=workspace)
            if is_frozen(batch)
        ]
        total = 0
        for batch in batches:
            if options['dry_run']:
                total += sum(
                    1 for line in provisional_plant_lines(batch) if settlement_of(line) is None
                )
                continue
            total += len(settle_batch_sales(
                batch, None, FulfillmentLineSettlement.Source.BACKFILL,
                options['reason'],
            ))
        verb = 'would settle' if options['dry_run'] else 'settled'
        self.stdout.write(f'{verb} {total} fulfillment line(s).')
