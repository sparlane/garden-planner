"""Prove the quantity backfill preserves posted nursery history."""

# pylint: disable=duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from inventory.models import InventoryItem
from locations.models import Location
from reporting.commerce import profitability_report
from tests.factories import make_specific_plant, make_stock_lot
from workspaces.models import get_current_workspace

from .test_concurrency import ReservationConcurrencyTestCase


class MeasuredHistoryMigrationTests(ReservationConcurrencyTestCase):
    """Migrate real predecessor rows, including recorded returns and refunds."""

    previous = [('sales', '0011_forward_sell_growing_stock')]
    latest = [('sales', '0013_measured_snapshot_constraints')]

    def test_backfill_changes_no_existing_commercial_value(self):
        """Every old column survives byte-for-byte apart from implicit ones."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.previous)
        try:
            apps = executor.loader.project_state(self.previous).apps
            self.seed_history(apps)
            names = ['SalesOrder', 'SalesOrderLine', 'SalesOrderAllocation', 'Fulfillment',
                     'FulfillmentLine', 'SalesReturn', 'SalesReturnLine', 'Payment', 'Refund', 'RefundLine']
            before = {name: list(apps.get_model('sales', name).objects.order_by('pk').values()) for name in names}
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(self.latest)
        current = executor.loader.project_state(self.latest).apps
        for name, rows in before.items():
            for original in rows:
                if name == 'SalesOrderAllocation' and original['quantity'] is None:
                    original['quantity'] = Decimal('1')
                migrated = current.get_model('sales', name).objects.values(*original).get(pk=original['id'])
                self.assertEqual(migrated, original, name)
        fulfillment = current.get_model('sales', 'FulfillmentLine')
        self.assertEqual(list(fulfillment.objects.order_by('pk').values_list('quantity', flat=True)), [1, 5])
        returned = current.get_model('sales', 'SalesReturnLine')
        self.assertEqual(list(returned.objects.order_by('pk').values_list('quantity', flat=True)), [1, 5])
        self.assertEqual(list(returned.objects.order_by('pk').values_list('cogs_amount', flat=True)), [Decimal('0.4321')] * 2)
        first_report = profitability_report(get_current_workspace(), {})
        # A safe rollback and repeat backfill must also leave profitability
        # unchanged, including the cost restoration attributed to each return.
        executor.migrate(self.previous)
        MigrationExecutor(connection).migrate(self.latest)
        repeated_report = profitability_report(get_current_workspace(), {})
        repeated_report.generated_at = first_report.generated_at
        self.assertEqual(repeated_report, first_report)

    def seed_history(self, apps):  # pylint: disable=too-many-locals
        """Write rows using only predecessor models and their original columns."""
        workspace = get_current_workspace()
        workspace.currency_code = 'NZD'
        workspace.save()
        plant = make_specific_plant(workspace=workspace)
        item = InventoryItem.objects.create(workspace=workspace, name='Historical pots', category='pot_container', base_unit='each')
        location = Location.objects.create(workspace=workspace, name='Historical store', code='HIST', location_type=Location.LocationType.STORAGE)
        lot = make_stock_lot(item=item, location=location, quantity=Decimal('10'))
        now = timezone.now()

        def create(name, **values):
            return apps.get_model('sales', name).objects.create(**values)

        metadata = {'workspace_id': workspace.pk, 'request_fingerprint': 'a' * 64}
        amounts = {field: Decimal('1.2345') for field in (
            'gross_ex_tax', 'discount_ex_tax', 'subtotal_ex_tax', 'tax_total', 'total_incl_tax',
        )}
        order = create('SalesOrder', workspace_id=workspace.pk, order_number='SO-HISTORY',
                       order_date=now.date(), currency_code='NZD', status='fulfilled')
        fulfillment = create('Fulfillment', **metadata, order=order, fulfillment_number='FUL-HISTORY',
                             fulfilled_at=now, operation_key=uuid4())
        returned = create('SalesReturn', **metadata, order=order, returned_at=now, reason='Historic return', operation_key=uuid4())
        payment = create('Payment', **metadata, order=order, paid_on=now.date(), amount=Decimal('7.6543'),
                         currency_code='NZD', method='cash', operation_key=uuid4())
        refund = create('Refund', **metadata, order=order, payment=payment, sales_return=returned,
                        refunded_at=now, amount=Decimal('2.4690'), currency_code='NZD',
                        reason='Historic credit', operation_key=uuid4())
        for quantity, target in [(1, {'plant_id': plant.pk}), (5, {'stock_lot_id': lot.pk, 'source_location_id': location.pk, 'quantity': 5})]:
            seedling = quantity == 1
            line = create('SalesOrderLine', order=order, line_type='seedling' if seedling else 'lot_quantity',
                          variety_id=plant.batch.variety_id if seedling else None, item_id=None if seedling else item.pk,
                          description='Historical sale', quantity=quantity, unit_price=Decimal('2.3456'),
                          tax_rate=Decimal('15'), tax_treatment='standard')
            allocation = create('SalesOrderAllocation', line=line, status='returned', **target)
            sold = create('FulfillmentLine', fulfillment=fulfillment, allocation=allocation,
                          commercial_position=1, currency_code='NZD', cogs_amount=Decimal('0.4321'), **amounts)
            create('SalesReturnLine', sales_return=returned, fulfillment_line=sold, outcome='available', destination_id=location.pk)
            create('RefundLine', refund=refund, fulfillment_line=sold, **amounts)
