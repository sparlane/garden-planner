"""Sale previews agree with departure posting without mutating the ledger."""
# pylint: disable=duplicate-code,protected-access

from decimal import Decimal
from uuid import uuid4

from django.test.utils import CaptureQueriesContext
from django.db import connection

from applications.services import post_application
from plantings.counted_fills import plant_counted_fill
from sales.commerce import _plant_cost, post_fulfillment
from sales.models import SalesOrder
from sales.test_commerce import CommerceFixtureTestCase
from seedtrays.container_fills import open_numbered_fill
from seedtrays.test_pot_media import PotMediaMixin
from inventory.ledger import IndividualizationRequest, individualize_lot_units
from tests.factories import make_specific_plant_location

from .services import batch_cost_breakdown, plant_cost_breakdown
from .models import CostAllocation


class PendingCostTests(PotMediaMixin, CommerceFixtureTestCase):  # pylint: disable=too-many-ancestors
    """Exercise the real commerce path for counted and shared numbered pots."""

    def setUp(self):
        super().setUp()
        self.setup_media()

    def prepare(self, count=1, counted=False):
        """Stand saleable plants in fifty litres of two-dollar media."""
        plants = [self.available_plant()]
        plants += [self.available_plant(batch=plants[0].batch, cell_planting=plants[0].cell_planting) for _ in range(count - 1)]
        if counted:
            post_application(self.draft(), self.user)
            plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk for plant in plants])
        else:
            unit, = individualize_lot_units(self.workspace, self.user, IndividualizationRequest(self.pots, self.store, 1))
            type(unit).objects.filter(pk=unit.pk).update(acquisition_cost=Decimal('5'))
            self.fill = open_numbered_fill(self.workspace, self.user, unit)
            post_application(self.draft(), self.user)
            for plant in reversed(plants):
                make_specific_plant_location(specific_plant=plant, location_type='container_unit',
                                             container_unit=unit, seed_tray_cell=None)
        return plants

    def assert_dispatch(self, plants, with_pot):
        """Compare the selected preview with each immutable fulfillment amount."""
        previews = {plant.pk: plant_cost_breakdown(plant) for plant in plants}
        order, allocations = self.confirmed_order(plants)
        ids = [row['pk'] for row in allocations]
        sale = post_fulfillment(SalesOrder.objects.get(pk=order['pk']), self.user,
                                operation_key=uuid4(), allocation_ids=ids, container_allocations=ids if with_pot else [])
        for line in sale.lines.select_related('allocation'):
            self.assertEqual(line.cogs_amount, Decimal(previews[line.allocation.plant_id][
                'sale_with_pot' if with_pot else 'sale_without_pot']))

    def test_numbered_dispatch_equality(self):
        """One plant carries the entire held mix and optionally the pot."""
        plants = self.prepare()
        preview = plant_cost_breakdown(plants[0])
        self.assertEqual(preview['provisional_value'], '0.0000')
        self.assertEqual(preview['pending'][0]['amount'], '100.0000')
        self.assertEqual(preview['sale_without_pot'], '100.0000')
        self.assert_dispatch(plants, True)

    def test_numbered_without_pot_equality(self):
        """Leaving the pot still consumes the media, exactly once."""
        self.assert_dispatch(self.prepare(), False)

    def test_counted_dispatch_equality(self):
        """Unplanted pots keep their shares of the media."""
        self.assert_dispatch(self.prepare(counted=True), True)

    def test_counted_without_pot_equality(self):
        """A knocked-out counted plant does not consume the pot."""
        self.assert_dispatch(self.prepare(counted=True), False)

    def test_shared_dispatch_equality(self):
        """Two occupants share one pot and must depart together."""
        plants = self.prepare(count=2)
        for plant in plants:
            preview = plant_cost_breakdown(plant)
            self.assertEqual(preview['pot_requires_plants'], [row.pk for row in plants])
            self.assertEqual(preview['pending'][1]['amount'], '2.5000')
        self.assert_dispatch(plants, True)

    def test_thirds_preserve_different_media_and_pot_order(self):
        """Media reserves placement order; dispatch splits pots in plant order."""
        self.assert_dispatch(self.prepare(count=3), True)

    def test_incomplete_history_has_no_sale_total(self):
        """A missing frozen participant cannot be costed as free media."""
        plant, = self.prepare()
        type(self.fill).objects.filter(pk=self.fill.pk).update(plant_share_count=2)
        preview = plant_cost_breakdown(plant)
        self.assertTrue(preview['pending'][0]['not_yet_allocatable'])
        self.assertIsNone(preview['sale_without_pot'])
        self.assertIsNone(preview['sale_with_pot'])

    def test_unknown_media_has_no_sale_total(self):
        """Unpriced mix cannot produce a deceptively small sale preview."""
        plant, = self.prepare()
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        preview = plant_cost_breakdown(plant)
        self.assertTrue(preview['pending'][0]['unknown_cost'])
        self.assertIsNone(preview['sale_without_pot'])
        self.assertIsNone(preview['sale_with_pot'])

    def test_unknown_pot_only_blocks_with_pot_total(self):
        """The operator can still cost a knocked-out sale of an unpriced pot."""
        plant, = self.prepare()
        unit = self.fill.inventory_unit
        type(unit).objects.filter(pk=unit.pk).update(acquisition_cost=None)
        preview = plant_cost_breakdown(plant)
        self.assertTrue(preview['pending'][1]['unknown_cost'])
        self.assertEqual(preview['sale_without_pot'], '100.0000')
        self.assertIsNone(preview['sale_with_pot'])

    def test_preview_is_read_only_and_commerce_still_reads_committed(self):
        """A read never posts held cost or changes batch or commerce values."""
        plant, = self.prepare()
        before = batch_cost_breakdown(plant.batch)
        layers = list(CostAllocation.objects.values())
        with CaptureQueriesContext(connection) as queries:
            plant_cost_breakdown(plant)
        self.assertFalse(any(query['sql'].lstrip().split()[0] in ('INSERT', 'UPDATE', 'DELETE') for query in queries))
        self.assertEqual(list(CostAllocation.objects.values()), layers)
        self.assertEqual(batch_cost_breakdown(plant.batch), before)
        self.assertEqual(_plant_cost(plant), (Decimal('0'), True))

    def test_tray_has_zero_pending_and_explains_why(self):
        """Tray media already reaches the ledger and a tray is not sold."""
        plant = self.available_plant()
        make_specific_plant_location(specific_plant=plant, seed_tray_cell=plant.cell_planting.cell)
        before = _plant_cost(plant)
        preview = plant_cost_breakdown(plant)
        self.assertEqual([row['amount'] for row in preview['pending']], ['0.0000', '0.0000'])
        self.assertIn('Tray media is already committed', preview['pending'][0]['reason'])
        self.assertEqual(_plant_cost(plant), before)
