"""Pot media stays on its fill until reclaimed or explicitly discarded."""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from applications.models import InputApplicationTarget
from applications.services import (
    ApplicationRequest, LineRequest, TargetRequest, create_application_draft,
    post_application, reverse_application,
)
from inventory.ledger import IndividualizationRequest, individualize_lot_units, physical_balance, unpromised_bulk
from sales.test_concurrency import ReservationConcurrencyTestCase
from sales.test_counted_lines import CountedStockTestCase
from tests.factories import (
    make_inventory_item, make_location, make_production_batch,
    make_seed_tray_generation, make_specific_plant_location, make_stock_lot,
)
from workspaces.models import Workspace, get_current_workspace

from .container_fills import clean_empty_fill, clean_pot_fill, open_counted_fill, open_numbered_fill
from .generations import CloseRequest, MediaDisposition, contents_digest
from .pot_media import pot_fill_contents, pot_fill_cost_breakdown, pot_fill_media_departures


class PotMediaMixin:
    """An unused buffer of fifty pots and one known-cost media lot."""

    workspace = None
    store = None
    pots = None
    fill = None
    media_item = None
    media = None

    def setup_media(self):
        """Prepare the same media and pot stock for serial and concurrent cases."""
        self.workspace = get_current_workspace()
        self.store = make_location()
        self.pots = make_stock_lot(item=make_inventory_item(
            category='pot_container', tracking_mode='mixed', base_unit='each',
        ), location=self.store, quantity='100')
        self.fill = open_counted_fill(self.workspace, None, self.pots, self.store, 50)
        self.media_item = make_inventory_item(category='growing_media', base_unit='l')
        self.media = make_stock_lot(item=self.media_item, location=self.store, quantity='200', base_unit_cost=Decimal('2'))

    def draft(self, fill=None, **overrides):
        """Record the explicit quantity put into one fill, without naming a crop."""
        line = LineRequest(
            item=self.media_item, lot=self.media, applied_quantity='50',
            unit_code='l', usage_basis='manual',
            targets=(TargetRequest('container_fill', fill or self.fill),),
        )
        values = {'applied_at': timezone.now(), 'source_location': self.store, 'lines': (line,)}
        values.update(overrides)
        return create_application_draft(self.workspace, None, ApplicationRequest(**values))

    def clean(self, fill=None, **overrides):
        """Dispose of the fifty litres as waste unless given other dispositions."""
        values = {'reason': 'Wash the pots.', 'media': (MediaDisposition(self.media.pk, '50', 'waste'),)}
        values.update(overrides)
        return clean_pot_fill(self.workspace, None, fill or self.fill, CloseRequest(**values))


class PotMediaTests(PotMediaMixin, CountedStockTestCase):
    """Posting and cleaning preserve quantity, cost and the original fill identity."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_media_leaves_stock_at_posting_and_cost_stays_on_fifty_pots(self):
        """An unplanted buffer owns its media without manufacturing numbered pots."""
        application = self.draft()
        self.assertEqual(physical_balance(self.media, self.store), 200)
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'], 0)
        post_application(application, None)
        self.assertEqual(physical_balance(self.media, self.store), 150)
        self.assertEqual(physical_balance(self.pots, self.store), 100)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        self.assertEqual(pot_fill_contents(self.fill)[0]['base_quantity'], 50)
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'], 100)
        self.assertFalse(self.pots.serialized_units.exists())
        self.assertEqual(application.lines.get().targets.get().container_fill, self.fill)
        with self.assertRaises(ValidationError):
            clean_empty_fill(self.workspace, None, self.fill, reason='Clean')

    def test_numbered_media_preserves_pot_acquisition_cost(self):
        """The mix belongs to the fill even when the pot was numbered first."""
        unit = individualize_lot_units(self.workspace, None, IndividualizationRequest(self.pots, self.store, 1))[0]
        cost = unit.acquisition_cost
        fill = open_numbered_fill(self.workspace, None, unit)
        post_application(self.draft(fill), None)
        self.clean(fill)
        unit.refresh_from_db()
        self.assertEqual(unit.acquisition_cost, cost)
        self.assertEqual(pot_fill_cost_breakdown(fill)['production_loss'], 100)
        self.assertEqual(open_numbered_fill(self.workspace, None, unit).sequence, 2)

    def test_clean_splits_waste_and_reclaim_and_releases_containers(self):
        """Every applied litre is disposed of once, with recovered cost back on hand."""
        application = self.draft()
        post_application(application, None)
        destination = make_location()
        self.clean(media=(
            MediaDisposition(self.media.pk, '20', 'waste', 'Contaminated.'),
            MediaDisposition(self.media.pk, '30', 'reclaimed', 'Reusable.', destination),
        ))
        self.assertEqual(physical_balance(self.media, destination), 30)
        self.assertEqual(unpromised_bulk(self.pots, self.store), 100)
        report = pot_fill_cost_breakdown(self.fill)
        self.assertEqual(report['applied_cost'], 100)
        self.assertEqual(report['held_cost'], 0)
        self.assertEqual(report['production_loss'], 40)
        self.assertEqual(report['recovered_cost'], 60)
        residual = self.fill.residuals.get(disposition='reclaimed')
        self.assertEqual(residual.movement.movement_type, 'adjustment_gain')
        self.assertEqual(residual.unit_cost, 2)
        with self.assertRaises(ValidationError):
            reverse_application(application, None, 'Cannot return this mix twice.')
        with self.assertRaises(ValidationError):
            self.clean()
        self.assertEqual(self.fill.residuals.count(), 2)

    def test_reverse_before_clean_restores_stock_and_removes_held_cost(self):
        """Correcting an application leaves its target on file but no media to discard."""
        application = self.draft()
        post_application(application, None)
        reverse_application(application, None, 'Wrong fill.')
        self.assertEqual(physical_balance(self.media, self.store), 200)
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['held_cost'], 0)
        clean_empty_fill(self.workspace, None, self.fill, reason='Wash empty pots.')
        self.assertEqual(application.lines.get().targets.get().container_fill, self.fill)

    def test_incomplete_excess_and_invalid_recovery_roll_back_clean(self):
        """A failed clean neither releases pots nor writes a partial recovery."""
        post_application(self.draft(), None)
        other = Workspace.objects.create(name='Other nursery')
        for dispositions in (
            (), (MediaDisposition(self.media.pk, '49', 'waste'),),
            (MediaDisposition(self.media.pk, '51', 'waste'),),
            (MediaDisposition(self.media.pk, '50', 'returned'),),
            (MediaDisposition(self.media.pk, '50', 'reclaimed'),),
            (MediaDisposition(self.media.pk, '50', 'reclaimed', destination=make_location(workspace=other)),),
        ):
            with self.subTest(dispositions=dispositions), self.assertRaises(ValidationError):
                self.clean(media=dispositions)
        self.assertFalse(self.fill.residuals.exists())
        self.assertEqual(unpromised_bulk(self.pots, self.store), 50)
        self.assertEqual(self.fill.events.count(), 1)

    def test_stale_clean_and_backdated_application_are_refused(self):
        """Neither dates nor a stale confirmation can hide media added to a fill."""
        digest = contents_digest({'plants': [], 'seeds': [], 'media': pot_fill_contents(self.fill)})
        with self.assertRaises(ValidationError):
            self.draft(applied_at=self.fill.opened_at - timedelta(seconds=1))
        application = self.draft()
        post_application(application, None)
        with self.assertRaises(ValidationError):
            self.clean(digest=digest)
        with self.assertRaises(ValidationError):
            self.clean(occurred_at=self.fill.opened_at)

    def test_draft_cannot_post_after_clean_or_retarget_to_a_new_fill(self):
        """A clean may discard a draft's proposal; posting must recheck its saved fill."""
        application = self.draft()
        clean_empty_fill(self.workspace, None, self.fill, reason='Never applied.')
        open_counted_fill(self.workspace, None, self.pots, self.store, 50)
        with self.assertRaises(ValidationError):
            post_application(application, None)
        self.assertEqual(physical_balance(self.media, self.store), 200)
        self.assertEqual(application.lines.get().targets.get().container_fill, self.fill)

    def test_multiple_lines_accumulate_applied_media_but_not_spillage(self):
        """The clean accounts for media put into pots, excluding application waste."""
        line = LineRequest(
            self.media_item, self.media, '25', 'l', usage_basis='manual',
            targets=(TargetRequest('container_fill', self.fill),),
            waste_quantity='5', waste_reason='Spilled on the floor.',
        )
        post_application(self.draft(lines=(line, line)), None)
        self.assertEqual(physical_balance(self.media, self.store), 140)
        self.assertEqual(pot_fill_contents(self.fill)[0]['base_quantity'], 50)
        self.clean()
        self.assertEqual(pot_fill_cost_breakdown(self.fill)['production_loss'], 100)

    def test_pots_cannot_be_consumed_as_fill_media(self):
        """A fill lends containers; its input document can consume only media."""
        line = LineRequest(
            self.pots.item, self.pots, '50', 'each', usage_basis='manual',
            targets=(TargetRequest('container_fill', self.fill),),
        )
        with self.assertRaisesMessage(ValidationError, 'growing media only'):
            self.draft(lines=(line,))
        self.assertEqual(physical_balance(self.pots, self.store), 100)

    def test_unknown_cost_stays_unknown(self):
        """An unpriced lot must not turn into free media in the fill report."""
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        post_application(self.draft(), None)
        report = pot_fill_cost_breakdown(self.fill)
        self.assertTrue(report['unknown_cost'])
        self.assertIsNone(report['held_cost'])
        self.clean()
        self.assertIsNone(pot_fill_cost_breakdown(self.fill)['production_loss'])

    def test_mixed_targets_batches_trays_and_partial_weights_are_refused(self):
        """There is no implicit cost-sharing basis or crop-pool fallback."""
        batch = make_production_batch()
        with self.assertRaises(ValidationError):
            self.draft(batch=batch)
        with self.assertRaises(ValidationError):
            self.draft(make_seed_tray_generation())
        application = self.draft()
        line = application.lines.get()
        for targets in (
            (TargetRequest('container_fill', self.fill, Decimal('0.5')),),
            (TargetRequest('container_fill', self.fill), TargetRequest('batch', batch)),
        ):
            with self.assertRaises(ValidationError):
                self.draft(lines=(LineRequest(self.media_item, self.media, '50', 'l', usage_basis='manual', targets=targets),))
        target = line.targets.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InputApplicationTarget.objects.filter(pk=target.pk).update(inventory_unit_id=self.number(self.pots, 1)[0].pk)

    def test_clean_refuses_reclaiming_media_taken_by_a_plant(self):
        """A departed plant's share cannot be misclassified as discarded media."""
        unit = individualize_lot_units(self.workspace, None, IndividualizationRequest(self.pots, self.store, 1))[0]
        fill = open_numbered_fill(self.workspace, None, unit)
        post_application(self.draft(fill), None)
        placement = make_specific_plant_location(location_type='container_unit', container_unit=unit, seed_tray_cell=None)
        placement.ended = timezone.now()
        placement.save()
        with self.assertRaisesMessage(ValidationError, 'nothing left over'):
            self.clean(fill)
        self.assertFalse(fill.residuals.exists())
        clean_empty_fill(self.workspace, None, fill, reason='Wash the vacated pot.')
        self.assertEqual(pot_fill_cost_breakdown(fill)['departed_cost'], 100)
        self.assertEqual(pot_fill_cost_breakdown(fill)['production_loss'], 0)
        self.assertEqual(physical_balance(self.media, self.store), 150)
        self.assertEqual(open_numbered_fill(self.workspace, None, unit).sequence, 2)


class PotMediaDepartureTests(PotMediaMixin, CountedStockTestCase):
    """Departed media stays with the original plants through cleaning and reuse."""

    def setUp(self):
        super().setUp()
        self.setup_media()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def shared_numbered_fill(self):
        """Put three plants in a pot containing fifty litres of known-cost mix."""
        unit = self.number(self.pots, 1)[0]
        fill = open_numbered_fill(self.workspace, None, unit)
        post_application(self.draft(fill), None)
        placements = [make_specific_plant_location(
            location_type='container_unit', container_unit=unit, seed_tray_cell=None,
        ) for _ in range(3)]
        return fill, placements

    def test_departures_take_exact_thirds_and_held_cost_reconciles(self):
        """Sequential departures never give the last plant its siblings' media."""
        fill, placements = self.shared_numbered_fill()
        self.assertEqual(pot_fill_media_departures(fill), [])
        self.assertEqual(pot_fill_cost_breakdown(fill)['held_cost'], 100)
        for count, placement in enumerate(placements, start=1):
            placement.ended = timezone.now()
            placement.save()
            rows = pot_fill_media_departures(fill)
            self.assertEqual(len(rows), count)
            self.assertEqual({row['plant'] for row in rows}, {p.specific_plant_id for p in placements[:count]})
            self.assertTrue(all(row['base_quantity'] == Fraction(50, 3) for row in rows))
            self.assertTrue(all(row['lot'] == self.media and row['unit_cost'] == 2 for row in rows))
            report = pot_fill_cost_breakdown(fill)
            self.assertFalse(report['unknown_allocation'])
            self.assertEqual(report['departed_cost'] + report['held_cost'], report['applied_cost'])
        self.assertEqual(report['departed_cost'], 100)
        self.assertEqual(report['held_cost'], 0)
        self.assertEqual(sum(row['base_quantity'] for row in rows), 50)
        self.assertEqual(physical_balance(self.media, self.store), 150)

    def test_legacy_departure_reports_unknown_allocation(self):
        """Missing historical shares cannot turn used media into held stock."""
        fill, placements = self.shared_numbered_fill()
        type(placements[0]).objects.filter(pk=placements[0].pk).update(ended=timezone.now())
        self.assertIsNone(pot_fill_media_departures(fill)[0]['base_quantity'])
        report = pot_fill_cost_breakdown(fill)
        self.assertTrue(report['unknown_allocation'])
        self.assertFalse(report['unknown_cost'])
        self.assertEqual(report['applied_cost'], 100)
        self.assertIsNone(report['held_cost'])
        self.assertIsNone(report['departed_cost'])

    def test_clean_after_all_thirds_depart_preserves_allocation(self):
        """Cleaning and refilling do not reclaim or reassign the old plants' mix."""
        fill, placements = self.shared_numbered_fill()
        digest = contents_digest({'plants': [], 'seeds': [], 'media': pot_fill_contents(fill)})
        for placement in placements:
            placement.ended = timezone.now()
            placement.save()
        before = pot_fill_media_departures(fill)
        with self.assertRaisesMessage(ValidationError, 'changed'):
            self.clean(fill, media=(), digest=digest)
        with self.assertRaisesMessage(ValidationError, 'cannot precede'):
            self.clean(fill, media=(), occurred_at=placements[0].ended)
        self.clean(fill, media=())
        new_fill = open_numbered_fill(self.workspace, None, fill.inventory_unit)
        post_application(self.draft(new_fill), None)
        self.assertEqual(pot_fill_media_departures(fill), before)
        self.assertFalse(fill.residuals.exists())
        report = pot_fill_cost_breakdown(fill)
        self.assertEqual(report['held_cost'], 0)
        self.assertEqual(report['departed_cost'], 100)
        self.assertEqual(report['recovered_cost'], 0)

    def test_clean_requires_every_participant_to_depart(self):
        """A fixed share basis does not make a still-occupied pot cleanable."""
        fill, placements = self.shared_numbered_fill()
        placements[0].ended = timezone.now()
        placements[0].save()
        with self.assertRaisesMessage(ValidationError, 'Move the plants'):
            self.clean(fill, media=())
        self.assertFalse(fill.residuals.exists())

    def test_clean_refuses_legacy_departures_without_frozen_shares(self):
        """Old unallocated media must not be turned into a recovery or a loss."""
        fill, placements = self.shared_numbered_fill()
        type(placements[0]).objects.filter(container_fill=fill).update(ended=timezone.now())
        with self.assertRaisesMessage(ValidationError, 'departure accounting'):
            self.clean(fill)
        self.assertFalse(fill.residuals.exists())

    def test_clean_refuses_incomplete_frozen_participation(self):
        """An inconsistent denominator cannot silently release unclaimed media."""
        fill, placements = self.shared_numbered_fill()
        for placement in placements:
            placement.ended = timezone.now()
            placement.save()
        type(fill).objects.filter(pk=fill.pk).update(plant_share_count=4)
        with self.assertRaisesMessage(ValidationError, 'do not match'):
            self.clean(fill, media=())
        self.assertFalse(fill.residuals.exists())

    def test_unpriced_departures_keep_exact_quantities_and_unknown_cost(self):
        """An unknown price does not obscure the known physical media share."""
        fill, placements = self.shared_numbered_fill()
        type(self.media).objects.filter(pk=self.media.pk).update(base_unit_cost=None)
        placements[0].ended = timezone.now()
        placements[0].save()
        row = pot_fill_media_departures(fill)[0]
        self.assertEqual(row['base_quantity'], Fraction(50, 3))
        self.assertIsNone(row['unit_cost'])
        report = pot_fill_cost_breakdown(fill)
        self.assertTrue(report['unknown_cost'])
        self.assertIsNone(report['departed_cost'])
        self.assertIsNone(report['held_cost'])

    def test_rest_resolves_fill_identity_and_scopes_it_to_workspace(self):
        """The application API round-trips a fill target without a fake unit or crop."""
        payload = {
            'applied_at': timezone.now().isoformat(), 'source_location': self.store.pk,
            'lines': [{'item': self.media_item.pk, 'lot': self.media.pk, 'applied_quantity': '50',
                       'unit_code': 'l', 'usage_basis': 'manual',
                       'targets': [{'target_type': 'container_fill', 'target': self.fill.pk}]}],
        }
        response = self.client.post('/applications/input-applications/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        target = response.data['lines'][0]['targets'][0]
        self.assertEqual(target['target_type'], 'container_fill')
        self.assertEqual(target['target'], self.fill.pk)
        self.client.post(f'/applications/input-applications/{response.data["pk"]}/post/', {}, format='json')
        self.assertEqual(physical_balance(self.media, self.store), 150)
        type(self.fill).objects.filter(pk=self.fill.pk).update(workspace=Workspace.objects.create(name='Foreign'))
        response = self.client.post('/applications/input-applications/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)


@skipUnlessDBFeature('has_select_for_update')
class PotMediaConcurrencyTests(PotMediaMixin, ReservationConcurrencyTestCase):
    """Posting, cleaning and reversal agree on one locked view of fill contents."""

    def setUp(self):
        super().setUp()
        self.setup_media()

    def race(self, first, second):
        """Exercise independent transactions and surface deadlocks as test failures."""
        barrier = Barrier(2)

        def attempt(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                operation()
                return 'done'
            except ValidationError:
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, [first, second])), ['done', 'rejected'])

    def test_post_and_empty_clean_cannot_both_succeed(self):
        """The clean either sees posted media or makes the pending draft unpostable."""
        application = self.draft()
        self.race(lambda: post_application(application, None), lambda: clean_empty_fill(self.workspace, None, self.fill, reason='Clean'))
        application.refresh_from_db()
        self.fill.refresh_from_db()
        self.assertEqual(application.status == 'posted', self.fill.status == 'open')

    def test_clean_and_reverse_cannot_return_the_same_media_twice(self):
        """Reclaim and application reversal compete on the fill before touching media."""
        application = self.draft()
        post_application(application, None)
        self.race(
            lambda: self.clean(media=(MediaDisposition(self.media.pk, '50', 'reclaimed', destination=self.store),)),
            lambda: reverse_application(application, None, 'Correction'),
        )
        self.assertEqual(physical_balance(self.media, self.store), 200)
