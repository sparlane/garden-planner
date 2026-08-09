"""The endpoints a tray screen uses to fill, clean, and review a generation.

These also cover the archive contract, which the frontend depends on and no
JavaScript test runner exists to check: a cleaned fill's sowings leave the
tray's normal view without leaving the database.
"""
# pylint: disable=duplicate-code

from decimal import Decimal

from django.utils import timezone

from applications.services import (
    ApplicationRequest,
    LineRequest,
    TargetRequest,
    create_application_draft,
    post_application,
)
from inventory.models import InventoryItem
from inventory.units import UnitCode
from plantings.lifecycle import record_germination_event
from seeds.services import ensure_packet_inventory_identity
from tests.api import RESTContractTestCase
from tests.factories import (
    make_batch_for_packet,
    make_inventory_item,
    make_location,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_model,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
    make_stock_lot,
)
from workspaces.models import Workspace

from .generations import open_generation
from .models import SeedTrayGeneration


class GenerationRESTTestCase(RESTContractTestCase):
    """One two-cell tray, ready to be filled through the API."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=1)
        tray_model = make_seed_tray_model(cell_size_ml=40, x_cells=2, y_cells=1)
        self.tray = make_seed_tray(model=tray_model)
        self.cells = [
            make_seed_tray_cell(tray=self.tray, x_position=index)
            for index in range(2)
        ]

    def fill(self):
        """Fill the tray through the API and return the response body."""
        response = self.client.post(
            '/seedtrays/seedtraygenerations/',
            {'tray': self.tray.pk, 'notes': 'Peat-free mix.'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def sow(self, generation, quantity=4, allocations=((0, 2),)):
        """Sow into one fill, allocating the given (cell index, count) pairs."""
        packet = ensure_packet_inventory_identity(make_seed_packet())
        sowing = make_seed_tray_planting(
            seeds_used=packet,
            batch=make_batch_for_packet(packet),
            quantity=quantity,
            seed_tray=self.tray,
            generation=SeedTrayGeneration.objects.get(pk=generation),
        )
        for index, count in allocations:
            make_seed_tray_cell_planting(
                seed_tray_planting=sowing,
                cell=self.cells[index],
                quantity=count,
            )
        return sowing


class GenerationContractTests(GenerationRESTTestCase):
    """Filling, listing, and reading one fill of a tray."""

    @property
    def list_urls(self):
        """Return the generation collection route."""
        return ('/seedtrays/seedtraygenerations/',)

    def test_the_collection_requires_authentication(self):
        """Anonymous requests cannot read a workspace's tray history."""
        self.assert_authentication_required(self.list_urls)

    def test_the_collection_uses_the_common_list_contract(self):
        """Generations list the way every other collection does."""
        self.assert_list_contract(self.list_urls)

    def test_filling_a_tray_returns_the_new_fill(self):
        """The response is what a screen needs to show the tray is in use."""
        data = self.fill()

        self.assertEqual(data['tray'], self.tray.pk)
        self.assertEqual(data['sequence'], 1)
        self.assertEqual(data['status'], SeedTrayGeneration.Status.OPEN)
        self.assertEqual(data['origin'], SeedTrayGeneration.Origin.OPERATOR)
        self.assertEqual(data['notes'], 'Peat-free mix.')
        self.assertEqual(len(data['events']), 1)

    def test_filling_a_tray_that_is_already_in_use_is_refused(self):
        """The second fill would inherit the first one's crop."""
        self.fill()

        response = self.client.post(
            '/seedtrays/seedtraygenerations/',
            {'tray': self.tray.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('still open', str(response.data['tray']))

    def test_the_tray_reports_which_fill_it_is_on(self):
        """A screen has to be able to say the tray is empty."""
        response = self.client.get(f'/seedtrays/seedtrays/{self.tray.pk}/')
        self.assertIsNone(response.data['active_generation'])
        self.assertFalse(response.data['generation_review_required'])

        data = self.fill()

        response = self.client.get(f'/seedtrays/seedtrays/{self.tray.pk}/')
        self.assertEqual(response.data['active_generation'], data['pk'])

    def test_a_migrated_fill_is_flagged_on_the_tray(self):
        """The banner that blocks cleaning is driven by this."""
        SeedTrayGeneration.objects.filter(pk=self.fill()['pk']).update(
            origin=SeedTrayGeneration.Origin.LEGACY,
            review_state=SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

        response = self.client.get(f'/seedtrays/seedtrays/{self.tray.pk}/')

        self.assertTrue(response.data['generation_review_required'])

    def test_generations_can_be_filtered_by_tray_and_status(self):
        """The history panel asks for one tray's closed fills."""
        data = self.fill()
        other = make_seed_tray()
        open_generation(other, None)

        response = self.client.get(
            f'/seedtrays/seedtraygenerations/?tray={self.tray.pk}&status=open'
        )

        self.assertEqual([row['pk'] for row in response.data], [data['pk']])

    def test_a_bad_tray_filter_is_reported_as_a_field_error(self):
        """A typo should not read as an empty history."""
        response = self.client.get('/seedtrays/seedtraygenerations/?tray=maybe')

        self.assertEqual(response.status_code, 400)
        self.assertIn('tray', response.data)


class GenerationCleanContractTests(GenerationRESTTestCase):
    """The guided clean, its confirmation view, and its correction."""

    def setUp(self):
        super().setUp()
        self.location = make_location()
        self.media_item = make_inventory_item(
            base_unit=UnitCode.LITRE,
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
        )
        self.media_lot = make_stock_lot(
            item=self.media_item,
            location=self.location,
            quantity='50',
            base_unit_cost=Decimal('2'),
        )
        self.generation = self.fill()['pk']

    def apply_media(self):
        """Post one media application filling both cells of the tray."""
        application = create_application_draft(
            self.workspace,
            None,
            ApplicationRequest(
                applied_at=timezone.now(),
                source_location=self.location,
                lines=(LineRequest(
                    item=self.media_item,
                    lot=self.media_lot,
                    applied_quantity=Decimal('0.08'),
                    unit_code=UnitCode.LITRE,
                    targets=tuple(
                        TargetRequest('seed_tray_cell', cell)
                        for cell in self.cells
                    ),
                ),),
            ),
        )
        return post_application(application, None)[0]

    def contents(self):
        """Read the pre-clean confirmation view."""
        response = self.client.get(
            f'/seedtrays/seedtraygenerations/{self.generation}/contents/'
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def close(self, **overrides):
        """Post a fully specified clean built from the confirmation view."""
        contents = self.contents()
        payload = {
            'reason': 'End of the propagation run.',
            'digest': contents['digest'],
            'plants': [
                {'plant': row['pk'], 'outcome': 'failed', 'reason': 'Damped off.'}
                for row in contents['plants']
            ],
            'seeds': [
                {
                    'sowing': row['sowing'],
                    'quantity': str(row['quantity']),
                    'disposition': 'removed',
                    'reason': 'Swept up.',
                }
                for row in contents['seeds']
            ],
            'media': [
                {
                    'lot': row['lot'],
                    'quantity': row['base_quantity'],
                    'disposition': 'waste',
                    'reason': 'Tipped out.',
                }
                for row in contents['media']
            ],
        }
        payload.update(overrides)
        return self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/close/',
            payload,
            format='json',
        )

    def test_contents_report_everything_needing_a_decision(self):
        """The screen is built from this, so it has to be complete."""
        self.apply_media()
        sowing = self.sow(self.generation)
        plant = make_specific_plant(cell_planting=sowing.cell_plantings.get())
        record_germination_event(plant, None)
        make_specific_plant_location(specific_plant=plant)

        contents = self.contents()

        self.assertEqual([row['pk'] for row in contents['plants']], [plant.pk])
        self.assertEqual(
            [(row['sowing'], row['quantity']) for row in contents['seeds']],
            [(sowing.pk, 2)],
        )
        self.assertEqual(
            [(row['lot'], row['base_quantity']) for row in contents['media']],
            [(self.media_lot.pk, '0.080000000')],
        )
        self.assertTrue(contents['digest'])

    def test_cleaning_closes_the_fill(self):
        """The confirmed clean is what empties the tray."""
        self.apply_media()

        response = self.close()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data['generation']['status'],
            SeedTrayGeneration.Status.CLOSED,
        )
        self.assertIsNone(response.data['next_generation'])
        self.assertEqual(len(response.data['generation']['residuals']), 1)

    def test_an_incomplete_clean_is_refused(self):
        """Nothing is quietly assumed about what was left in the tray."""
        self.apply_media()

        response = self.close(media=[])

        self.assertEqual(response.status_code, 400)
        self.assertIn('media', response.data)
        self.assertEqual(
            SeedTrayGeneration.objects.get(pk=self.generation).status,
            SeedTrayGeneration.Status.OPEN,
        )

    def test_a_stale_confirmation_is_refused(self):
        """An operator must decide about what is actually in the tray."""
        contents = self.contents()
        self.sow(self.generation)

        response = self.close(digest=contents['digest'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('digest', response.data)

    def test_cleaning_twice_is_refused(self):
        """A resubmitted confirmation resolves nothing a second time."""
        self.close()

        response = self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/close/',
            {'reason': 'Again.'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('already closed', str(response.data))

    def test_the_tray_can_be_refilled_in_the_same_step(self):
        """Filling again is the usual next act."""
        response = self.close(open_next=True)

        following = response.data['next_generation']
        self.assertEqual(following['sequence'], 2)
        self.assertEqual(following['status'], SeedTrayGeneration.Status.OPEN)

    def test_a_migrated_fill_must_be_reviewed_before_it_is_cleaned(self):
        """Its contents may belong to two cycles nobody has separated."""
        SeedTrayGeneration.objects.filter(pk=self.generation).update(
            origin=SeedTrayGeneration.Origin.LEGACY,
            review_state=SeedTrayGeneration.ReviewState.NEEDS_REVIEW,
        )

        refused = self.close()
        reviewed = self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/review/',
            {'reason': 'Checked the sowing notebook.'},
            format='json',
        )

        self.assertEqual(refused.status_code, 400)
        self.assertIn('review_state', refused.data)
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(
            reviewed.data['review_state'],
            SeedTrayGeneration.ReviewState.NONE,
        )
        self.assertEqual(self.close().status_code, 200)

    def test_a_mistaken_clean_can_be_corrected(self):
        """The close stays on file next to the correction that undid it."""
        self.close()

        response = self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/reopen/',
            {'reason': 'Cleaned the wrong tray.'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], SeedTrayGeneration.Status.OPEN)
        types = [event['event_type'] for event in response.data['events']]
        self.assertEqual(types, ['opened', 'closed', 'reopened'])

    def test_a_correction_needs_a_reason(self):
        """Undoing an audited action without saying why is not an audit trail."""
        self.close()

        response = self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/reopen/',
            {'reason': ''},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('reason', response.data)

    def test_the_cost_breakdown_reaches_the_seedlings(self):
        """An operator can ask which media lot supplied each plant."""
        self.apply_media()
        sowing = self.sow(self.generation)
        plant = make_specific_plant(cell_planting=sowing.cell_plantings.get())
        record_germination_event(plant, None)

        response = self.client.get(
            f'/seedtrays/seedtraygenerations/{self.generation}/cost-breakdown/'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['applied_cost'], Decimal('0.160000000000'))
        self.assertEqual(
            [row['plant'] for row in response.data['plants']],
            [plant.pk],
        )
        self.assertFalse(response.data['unknown_cost'])


class GenerationArchiveTests(GenerationRESTTestCase):
    """A cleaned fill leaves the tray's normal view without leaving the data."""

    def setUp(self):
        super().setUp()
        self.generation = self.fill()['pk']
        self.sowing = self.sow(self.generation, quantity=2, allocations=((0, 2),))
        self.plant = make_specific_plant(
            cell_planting=self.sowing.cell_plantings.get(),
        )
        record_germination_event(self.plant, None)
        make_specific_plant_location(specific_plant=self.plant)
        self.client.post(
            f'/seedtrays/seedtraygenerations/{self.generation}/close/',
            {
                'reason': 'End of the run.',
                'plants': [
                    {'plant': self.plant.pk, 'outcome': 'failed', 'reason': 'Damped.'},
                ],
            },
            format='json',
        )

    def test_an_archived_sowing_leaves_the_tray_view(self):
        """The screen shows what is in the tray now, which is nothing."""
        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_history_brings_the_archived_sowing_straight_back(self):
        """The archive is a filter, so nothing was lost."""
        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/?history=true'
        )

        self.assertEqual([row['pk'] for row in response.data], [self.sowing.pk])
        self.assertEqual(response.data[0]['generation'], self.generation)

    def test_an_explicit_generation_reads_one_closed_fill(self):
        """Traceability asks for the fill, not for everything ever."""
        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
            f'?generation={self.generation}'
        )

        self.assertEqual([row['pk'] for row in response.data], [self.sowing.pk])

    def test_the_new_fill_starts_with_no_sowings(self):
        """Reusing the tray must not inherit the previous crop."""
        following = self.client.post(
            '/seedtrays/seedtraygenerations/',
            {'tray': self.tray.pk},
            format='json',
        ).data

        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
        )

        self.assertEqual(response.data, [])
        self.assertEqual(following['sequence'], 2)

    def test_an_archived_plant_leaves_the_tray_view_but_keeps_its_history(self):
        """Its cost and lifecycle stay readable through the old fill."""
        current = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/specificplants/'
        )
        history = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/specificplants/?history=true'
        )

        self.assertEqual(current.data, [])
        self.assertEqual([row['pk'] for row in history.data], [self.plant.pk])

    def test_a_sowing_predating_generations_stays_visible(self):
        """It has no fill to hide behind, so hiding it would lose it."""
        legacy = make_seed_tray_planting(seed_tray=self.tray, generation=None)

        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/'
        )

        self.assertEqual([row['pk'] for row in response.data], [legacy.pk])

    def test_a_bad_history_flag_is_reported_as_a_field_error(self):
        """A typo should not silently read as the default view."""
        response = self.client.get(
            f'/plantings/seedtray-data/{self.tray.pk}/plantings/?history=maybe'
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('history', response.data)
