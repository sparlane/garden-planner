"""Batch projections preserve media slots without consuming the physical pot."""
# pylint: disable=duplicate-code

from decimal import Decimal

from applications.services import post_application
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import SeedTrayPlanting, SpecificPlantLocation
from plantings.counted_fills import plant_counted_fill
from plantings.movement import move_specific_plant
from plantings.batches import finalize_batch_output
from sales.test_commerce import CommerceFixtureTestCase
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import make_stock_lot
from reporting.production import _batch_row

from .services import batch_cost_breakdown, reallocate_batch
from . import test_pot_pending


class BatchProjectionTests(PotMediaMixin, CommerceFixtureTestCase):  # pylint: disable=too-many-ancestors
    """Exercise projections through real fills and departures."""

    plants = test_pot_pending.PotPendingCostTests.plants
    unit = test_pot_pending.PotPendingCostTests.unit
    numbered = test_pot_pending.PotPendingCostTests.numbered
    dispatch = test_pot_pending.PotPendingCostTests.dispatch
    potted_on = test_pot_pending.PotPendingCostTests.potted_on

    def setUp(self):
        super().setUp()
        self.setup_media()

    def test_batch_media_survives_departure_without_double_counting(self):
        """A held share moves to committed cost; the projected total is stable."""
        plant, = self.plants(1)
        self.numbered([plant])
        before = batch_cost_breakdown(plant.batch)['projection']
        self.assertEqual(before['projected_total'], '100.0000')
        self.assertEqual(before['currencies'][0]['pending_media_subtotal'], '100.0000')
        next_unit = self.unit()
        move_specific_plant(plant, {'location_type': SpecificPlantLocation.CONTAINER_UNIT,
                                    'container_unit': next_unit}, self.user)
        reallocate_batch(plant.batch, self.user, 'manual_recalculate')
        after = batch_cost_breakdown(plant.batch)['projection']
        self.assertEqual(after['projected_total'], before['projected_total'])
        self.assertEqual(after['currencies'][0]['pending_media_subtotal'], '0.0000')

    def test_shared_fill_only_assigns_the_batch_share(self):
        """Other batches keep their slots and the rounding remainder."""
        plants = self.plants(3)
        self.numbered(plants)
        totals = [batch_cost_breakdown(plant.batch)['projection']['projected_total'] for plant in plants]
        self.assertEqual(sum(map(Decimal, totals)), Decimal('100'))

    def test_unknown_media_has_no_projected_or_survivor_total(self):
        """Unknown costs must not become a lower pricing estimate."""
        plant, = self.plants(1)
        self.numbered([plant])
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        row = _batch_row(plant.batch)
        self.assertIsNone(row['cost_projection']['projected_total'])
        self.assertIsNone(row['cost_per_surviving_plant'])
        self.assertEqual(row['surviving_output'], 1)

    def test_sold_plants_remain_in_survivor_denominator(self):
        """Selling the output cannot shrink the batch pricing denominator."""
        plant, = self.plants(1)
        self.numbered([plant])
        self.assertEqual(_batch_row(plant.batch)['cost_per_surviving_plant'], '100.0000')
        self.dispatch([plant])
        row = _batch_row(plant.batch)
        self.assertEqual(row['surviving_output'], 1)
        self.assertEqual(row['cost_per_surviving_plant'], '105.0000')

    def test_losses_raise_survivor_price_and_zero_survivors_have_no_price(self):
        """The pricing estimate recovers failed production across remaining plants."""
        first = self.available_plant()
        second = self.available_plant(cell_planting=first.cell_planting)
        self.numbered([first, second])
        self.assertEqual(_batch_row(first.batch)['cost_per_surviving_plant'], '50.0000')
        record_lifecycle_event(second, self.user, OutcomeRequest(EventType.FAILED, reason='Damped off.'))
        reallocate_batch(first.batch, self.user, 'manual_recalculate')
        row = _batch_row(first.batch)
        self.assertEqual(row['surviving_output'], 1)
        self.assertEqual(row['cost_per_surviving_plant'], '100.0000')
        record_lifecycle_event(first, self.user, OutcomeRequest(EventType.FAILED, reason='Damped off.'))
        self.assertIsNone(_batch_row(first.batch)['cost_per_surviving_plant'])

    def test_foreign_pending_media_keeps_its_currency(self):
        """A foreign input is known money in its own currency, without conversion."""
        plant, = self.plants(1)
        self.numbered([plant])
        type(self.media).objects.filter(pk=self.media.pk).update(currency_code='USD')
        projection = batch_cost_breakdown(plant.batch)['projection']
        self.assertEqual(projection['currency_code'], 'USD')
        self.assertEqual(projection['projected_total'], '100.0000')
        self.assertFalse(projection['unknown_cost'])

    def test_final_output_does_not_hide_held_media(self):
        """Finalized allocation is distinct from costs still pending departure."""
        plant, = self.plants(1)
        self.numbered([plant])
        SeedTrayPlanting.objects.filter(batch=plant.batch).update(removed=True)
        finalize_batch_output(plant.batch, self.user, 'All output recorded.')
        plant.batch.refresh_from_db()
        cost = batch_cost_breakdown(plant.batch)
        self.assertFalse(cost['provisional'])
        self.assertEqual(cost['projection']['projected_total'], '100.0000')

    def test_incomplete_participation_blocks_projection(self):
        """A missing historical sharing denominator cannot be guessed."""
        plant, = self.plants(1)
        fill = self.numbered([plant])
        type(fill).objects.filter(pk=fill.pk).update(plant_share_count=2)
        projection = batch_cost_breakdown(plant.batch)['projection']
        self.assertTrue(projection['not_yet_allocatable'])
        self.assertIsNone(projection['projected_total'])

    def test_mixed_committed_and_pending_costs_are_kept_apart(self):
        """Pending domestic media cannot be added to earlier foreign media."""
        plant, = self.plants(1)
        imported = make_stock_lot(item=self.media_item, location=self.store, quantity='100',
                                  base_unit_cost=Decimal('3'), currency_code='USD')
        self.potted_on(plant, imported)
        projection = batch_cost_breakdown(plant.batch)['projection']
        self.assertTrue(projection['mixed_currency'])
        self.assertIsNone(projection['projected_total'])
        self.assertEqual({row['currency_code']: row['projected_total'] for row in projection['currencies']},
                         {'USD': '150.0000', self.workspace.currency_code: '100.0000'})

    def test_unknown_committed_cost_blocks_known_pending_projection(self):
        """A known held share cannot repair an unpriced earlier input."""
        plant, = self.plants(1)
        unpriced = make_stock_lot(item=self.media_item, location=self.store, quantity='100', base_unit_cost=None)
        self.potted_on(plant, unpriced)
        projection = batch_cost_breakdown(plant.batch)['projection']
        self.assertTrue(projection['unknown_cost'])
        self.assertIsNone(projection['projected_total'])

    def test_counted_fill_only_charges_planted_slots(self):
        """An unplanted pot reserves its media outside this batch's cost."""
        plant, = self.plants(1)
        post_application(self.draft(), self.user)
        plant_counted_fill(self.workspace, self.user, self.fill, [plant.pk])
        projection = batch_cost_breakdown(plant.batch)['projection']
        self.assertEqual(projection['projected_total'], '2.0000')
