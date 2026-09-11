"""Numbered-pot departures become stable, exactly reconciled crop cost layers."""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.utils import timezone

from applications.services import LineRequest, TargetRequest, post_application
from sales.test_counted_lines import CountedStockTestCase
from seedtrays.container_fills import clean_empty_fill, open_numbered_fill
from seedtrays.test_pot_media import PotMediaMixin
from tests.factories import make_specific_plant_location

from .services import effective_allocations, reallocate_batch
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
