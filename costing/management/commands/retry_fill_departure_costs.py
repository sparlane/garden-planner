"""Recover committed fill departures whose automatic costing did not finish."""

from django.core.management.base import BaseCommand, CommandError

from costing.models import FillDepartureRecalculation
from costing.services import reallocate_fill_departure


class Command(BaseCommand):
    """Process a bounded set of durable requests, leaving failures available."""

    help = 'Retry pending container-fill departure cost recalculations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--workspace', type=int)

    def handle(self, *args, **options):
        if options['limit'] < 1:
            raise CommandError('--limit must be positive.')
        pending = FillDepartureRecalculation.objects.order_by('requested_at', 'pk')
        if options['workspace'] is not None:
            pending = pending.filter(placement__specific_plant__workspace_id=options['workspace'])
        placement_ids = list(pending.values_list('placement_id', flat=True)[:options['limit']])
        failed = 0
        for placement_id in placement_ids:
            try:
                reallocate_fill_departure(placement_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # One failed request must not prevent unrelated crops recovering.
                failed += 1
                self.stderr.write(f'Placement {placement_id}: {exc}')
        self.stdout.write(f'Processed {len(placement_ids) - failed} pending fill departures.')
        if failed:
            raise CommandError(f'{failed} fill departures remain pending after errors.')
