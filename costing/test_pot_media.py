"""Numbered-pot departures become stable, exactly reconciled crop cost layers."""
# pylint: disable=duplicate-code

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from applications.services import LineRequest, TargetRequest, post_application
from plantings.lifecycle import OutcomeRequest, record_lifecycle_event
from plantings.movement import move_specific_plant
from sales.test_counted_lines import CountedStockTestCase
from seedtrays.container_fills import clean_empty_fill, open_numbered_fill
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import make_specific_plant_location

from .models import FillDepartureRecalculation
from .services import effective_allocations, reallocate_batch, reallocate_fill_departure
from .sources import pot_media_sources


class PotMediaCostTests(PotMediaMixin, CountedStockTestCase):
    """Allocate each media line across crops before selecting departed plants."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.unit = self.number(self.pots, 1)[0]
        self.fill = open_numbered_fill(self.workspace, self.user, self.unit)
        line = LineRequest(
            self.media_item, self.media, '50', 'l', usage_basis='manual',
            targets=(TargetRequest('container_fill', self.fill),),
            waste_quantity='1', waste_reason='Spilled before filling.',
        )
        self.application = self.draft(lines=(line,))
        post_application(self.application, self.user)
        self.placements = [make_specific_plant_location(
            location_type='container_unit', container_unit=self.unit, seed_tray_cell=None,
        ) for _ in range(3)]

    def leave(self, placement):
        """End the recorded interval through the shared departure validation."""
        placement.ended = timezone.now()
        placement.save(update_fields=['ended'])

    def layers(self, placement):
        """Recalculate through the ordinary subledger and read only pot media."""
        batch = placement.specific_plant.batch
        reallocate_batch(batch, self.user, 'manual_recalculate')
        return [row for row in effective_allocations(batch)
                if row.application_line_id in self.application.lines.values_list('pk', flat=True)]

    def test_cross_batch_thirds_reconcile_and_never_change_at_later_exits(self):
        """Three independently recalculated crops cannot each round a third up."""
        for placement in self.placements:
            self.assertEqual(self.layers(placement), [])
        recorded = []
        for placement in reversed(self.placements):
            self.leave(placement)
            layer, = self.layers(placement)
            self.assertEqual(layer.specific_plant_id, placement.specific_plant_id)
            self.assertEqual(layer.source_type, 'application_line')
            recorded.append(layer)
            for previous in recorded:
                self.assertIsNone(reallocate_batch(previous.batch, self.user, 'manual_recalculate'))
        self.assertEqual(sum(row.base_quantity for row in recorded), 50)
        self.assertEqual(sum(row.amount for row in recorded), 100)
        self.assertEqual(sorted(row.amount for row in recorded), [Decimal('33.3333'), Decimal('33.3333'), Decimal('33.3334')])
        clean_empty_fill(self.workspace, self.user, self.fill, reason='Wash used pot.')
        open_numbered_fill(self.workspace, self.user, self.unit)
        for placement in self.placements:
            self.assertEqual(self.layers(placement)[0].pk, next(row.pk for row in recorded if row.specific_plant_id == placement.specific_plant_id))

    def test_siblings_keep_separate_line_layers(self):
        """A batch with several participants must not overwrite a sibling's cost."""
        batch = self.placements[0].specific_plant.batch
        for placement in self.placements:
            plant = placement.specific_plant
            type(plant).objects.filter(pk=plant.pk).update(batch=batch)
            placement.specific_plant.refresh_from_db()
            self.leave(placement)
        layers = self.layers(self.placements[0])
        self.assertEqual(len(layers), 3)
        self.assertEqual(sum(row.amount for row in layers), 100)

    def test_unknown_cost_preserves_quantity_without_free_media(self):
        """Unpriced mix creates unknown layers, not a zero-valued purchase."""
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        self.leave(self.placements[0])
        layer, = self.layers(self.placements[0])
        self.assertIsNone(layer.amount)
        self.assertIsNone(layer.unit_cost)
        self.assertEqual(layer.base_quantity, Decimal('16.666666667'))

    def test_legacy_departure_does_not_gain_an_inferred_layer(self):
        """Existing exits without recorded shares retain their old costing."""
        placement = self.placements[0]
        type(placement).objects.filter(pk=placement.pk).update(ended=timezone.now())
        self.assertEqual(self.layers(placement), [])

    def test_unrelated_fill_is_not_a_plant_input(self):
        """Only applied media on the recorded numbered fill reaches its plants."""
        line = LineRequest(
            self.media_item, self.media, '5', 'l', usage_basis='manual',
            targets=(TargetRequest('container_fill', open_numbered_fill(
                self.workspace, self.user, self.number(self.pots, 1)[0],
            )),), waste_quantity='1', waste_reason='Spilled.',
        )
        post_application(self.draft(lines=(line,)), self.user)
        self.leave(self.placements[0])
        sources = pot_media_sources(self.placements[0].specific_plant.batch)
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.source.pk, self.application.lines.get().pk)
        self.assertEqual(source.amount, Decimal('33.3334'))

    def test_departure_automatically_posts_after_commit(self):
        """The ordinary interval end posts costs only once its facts commit."""
        placement = self.placements[0]
        batch = placement.specific_plant.batch
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.leave(placement)
            self.assertEqual(effective_allocations(batch), [])
            self.assertTrue(FillDepartureRecalculation.objects.filter(placement=placement).exists())
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        layer, = effective_allocations(batch)
        self.assertEqual(layer.amount, Decimal('33.3334'))
        self.assertEqual(layer.run.trigger, 'fill_departure')
        self.assertIsNone(layer.run.created_by)
        self.assertEqual(effective_allocations(self.placements[1].specific_plant.batch), [])
        callbacks[0]()
        self.assertEqual([row.pk for row in effective_allocations(batch)], [layer.pk])
        with self.captureOnCommitCallbacks(execute=True) as repeated:
            placement.notes = 'Departure checked.'
            placement.save(update_fields=['notes'])
        self.assertEqual(repeated, [])

    def test_rolled_back_departure_never_posts_costs(self):
        """An outer move failure discards both the departure and its callback."""
        placement = self.placements[0]
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesMessage(ValueError, 'Cancel move'):
                with transaction.atomic():
                    self.leave(placement)
                    raise ValueError('Cancel move')
        self.assertEqual(callbacks, [])
        placement.refresh_from_db()
        self.fill.refresh_from_db()
        self.assertIsNone(placement.ended)
        self.assertIsNone(self.fill.plant_share_count)
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        self.assertEqual(effective_allocations(placement.specific_plant.batch), [])

    def test_move_posts_source_fill_media(self):
        """Moving to another pot charges the fill left behind automatically."""
        placement = self.placements[0]
        destination = self.number(self.pots, 1)[0]
        with self.captureOnCommitCallbacks(execute=True):
            move_specific_plant(placement.specific_plant, {
                'location_type': 'container_unit', 'container_unit': destination,
            }, self.user)
        layer, = effective_allocations(placement.specific_plant.batch)
        self.assertEqual(layer.application_line_id, self.application.lines.get().pk)
        self.assertEqual(layer.amount, Decimal('33.3334'))

    def test_final_outcome_posts_departure_media(self):
        """A failed plant also consumed its fixed share of the pot's mix."""
        placement = self.placements[0]
        with self.captureOnCommitCallbacks(execute=True):
            record_lifecycle_event(
                placement.specific_plant, self.user,
                OutcomeRequest('failed', reason='Did not survive.'),
            )
        layer, = effective_allocations(placement.specific_plant.batch)
        self.assertEqual(layer.amount, Decimal('33.3334'))

    def test_failed_callback_is_durable_and_command_recovers_it(self):
        """A cost failure does not turn a committed departure into a failed move."""
        placement = self.placements[0]
        with patch('costing.services.reallocate_batch', side_effect=RuntimeError('Costing unavailable')):
            with self.assertLogs('django.test', level='ERROR'):
                with self.captureOnCommitCallbacks(execute=True):
                    self.leave(placement)
        placement.refresh_from_db()
        self.assertIsNotNone(placement.ended)
        self.assertTrue(FillDepartureRecalculation.objects.filter(placement=placement).exists())
        self.assertEqual(effective_allocations(placement.specific_plant.batch), [])
        output = StringIO()
        call_command('retry_fill_departure_costs', stdout=output)
        self.assertIn('Processed 1', output.getvalue())
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        layer, = effective_allocations(placement.specific_plant.batch)
        self.assertEqual(layer.amount, Decimal('33.3334'))
        call_command('retry_fill_departure_costs', stdout=StringIO())
        self.assertEqual([row.pk for row in effective_allocations(layer.batch)], [layer.pk])

    def test_retry_recovers_costs_already_posted_before_interruption(self):
        """A crash between posting layers and clearing work cannot double cost."""
        placement = self.placements[0]
        self.leave(placement)
        layer, = self.layers(placement)
        self.assertTrue(FillDepartureRecalculation.objects.exists())
        self.assertIsNone(reallocate_fill_departure(placement.pk))
        self.assertFalse(FillDepartureRecalculation.objects.exists())
        self.assertEqual([row.pk for row in effective_allocations(layer.batch)], [layer.pk])

    def test_retry_failure_keeps_request_and_processes_other_departures(self):
        """One failing crop does not prevent another pending crop recovering."""
        first, second = self.placements[:2]
        self.leave(first)
        self.leave(second)

        def retry(placement_id):
            if placement_id == first.pk:
                raise RuntimeError('Still unavailable')
            return reallocate_fill_departure(placement_id)

        with patch('costing.management.commands.retry_fill_departure_costs.reallocate_fill_departure', side_effect=retry):
            with self.assertRaisesMessage(CommandError, '1 fill departures remain pending'):
                call_command('retry_fill_departure_costs', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(list(FillDepartureRecalculation.objects.values_list('placement_id', flat=True)), [first.pk])
        self.assertEqual(len(effective_allocations(second.specific_plant.batch)), 1)

    def test_retry_limit_and_workspace_filter(self):
        """Maintenance can restrict work and refuses an invalid batch size."""
        for placement in self.placements:
            self.leave(placement)
        with self.assertRaisesMessage(CommandError, '--limit must be positive'):
            call_command('retry_fill_departure_costs', limit=0)
        call_command('retry_fill_departure_costs', workspace=-1, stdout=StringIO())
        self.assertEqual(FillDepartureRecalculation.objects.count(), 3)
        call_command('retry_fill_departure_costs', workspace=self.workspace.pk, limit=1, stdout=StringIO())
        self.assertEqual(FillDepartureRecalculation.objects.count(), 2)
