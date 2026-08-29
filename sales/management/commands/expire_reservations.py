"""Release the workspace's reservations whose recorded hold time has passed."""

from django.core.management.base import BaseCommand

from sales.expiry import SWEEP_REASON, due_reservations, expire_due_reservations
from workspaces.models import get_current_workspace


class Command(BaseCommand):
    """Run one expiry sweep over the configured workspace.

    Meant for cron or a container schedule. It is safe to run as often as the
    deployment likes: a run with nothing due writes nothing, and two runs that
    overlap cannot expire the same hold twice.
    """

    help = (
        'Expire sales reservations whose recorded expiry has passed, returning '
        'the held plants and trays to saleable stock. Use --dry-run to list '
        'what would lapse without changing anything.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            help='List the due reservations and exit without expiring them.',
        )
        parser.add_argument(
            '--reason',
            default=SWEEP_REASON,
            help='Reason recorded against each expiry in the reservation history.',
        )

    def handle(self, *args, **options):
        workspace = get_current_workspace()
        if options['dry_run']:
            due = list(due_reservations(workspace))
            for allocation in due:
                self.stdout.write(self._describe(allocation))
            self.stdout.write(f'{len(due)} reservation(s) due for expiry.')
            return
        expired = expire_due_reservations(workspace, reason=options['reason'])
        for allocation in expired:
            self.stdout.write(self._describe(allocation))
        self.stdout.write(self.style.SUCCESS(f'Expired {len(expired)} reservation(s).'))

    @staticmethod
    def _describe(allocation):
        """Name one hold by its order and the exact stock it was holding."""
        target = (
            f'plant {allocation.plant_id}' if allocation.plant_id
            else f'unit {allocation.inventory_unit_id}'
        )
        return (
            f'{allocation.line.order.order_number}: allocation {allocation.pk} '
            f'holding {target}, expired {allocation.expires_at.isoformat()}'
        )
