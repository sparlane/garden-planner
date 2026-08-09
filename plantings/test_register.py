"""Tests for the nursery plant register.

The register's promise is that its counts describe its filters rather than its
current page, so most of these tests compare a total against the rows the same
filter returns.
"""

# pylint: disable=duplicate-code

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.api import RESTContractTestCase
from tests.factories import (
    make_garden_square,
    make_location,
    make_plant_variety,
    make_production_batch,
    make_seed_packet,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_cell_planting,
    make_seed_tray_generation,
    make_seed_tray_model,
    make_seed_tray_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from locations.models import Location
from workspaces.models import Workspace, get_current_workspace

from .lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from . import register_rest
from .models import SpecificPlantLocation


class RegisterTestCase(RESTContractTestCase):
    """Shared nursery workspace and plant-building helpers."""

    url = '/plantings/register/'
    ids_url = '/plantings/register/ids/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.operator = get_user_model().objects.create_user(username='register-operator')
        self.start = timezone.now() - timedelta(days=40)

    def make_batch(self, **overrides):
        """Create an active batch whose sowing can carry plants."""
        return make_production_batch(**overrides)

    def make_plant(self, batch=None, germinated=None, tray=None):
        """Germinate one plant from a new cell allocation on a batch."""
        packet = make_seed_packet()
        if batch is None:
            batch = self.make_batch(variety=packet.seeds.plant_variety)
        planting = make_seed_tray_planting(
            batch=batch,
            seeds_used=packet,
            seed_tray=tray or make_seed_tray(),
        )
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=planting)
        plant = make_specific_plant(
            cell_planting=cell_planting,
            germinated=germinated or self.start,
        )
        record_germination_event(plant, self.operator)
        return plant

    def make_plant_in_state(self, state, **kwargs):
        """Germinate a plant and drive it to one derived lifecycle state."""
        plant = self.make_plant(**kwargs)
        outcomes = {
            LifecycleState.AVAILABLE: EventType.READY,
            LifecycleState.RETAINED: EventType.RETAINED,
            LifecycleState.DONATED: EventType.DONATED,
            LifecycleState.FAILED: EventType.FAILED,
            LifecycleState.CULLED: EventType.CULLED,
            LifecycleState.HARVESTED: EventType.HARVEST_FINISHED,
        }
        if state != LifecycleState.GROWING:
            record_lifecycle_event(
                plant,
                self.operator,
                OutcomeRequest(outcomes[state], occurred_at=self.start + timedelta(days=1)),
            )
        return plant

    def page(self, **params):
        """Request one page of the register and assert it succeeded."""
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def row_ids(self, payload):
        """Return the plant IDs in one page of results."""
        return [row['pk'] for row in payload['results']]


class RegisterContractTests(RegisterTestCase):
    """The register's shape, permissions, and profile boundary."""

    def test_the_register_requires_authentication(self):
        """Plant inventory is workspace data, not a public catalog."""
        self.assert_authentication_required([self.url, self.ids_url])

    def test_the_register_returns_a_page_and_its_totals_together(self):
        """One request answers both questions the screen asks at once."""
        self.make_plant()
        payload = self.page()
        self.assertEqual(payload['count'], 1)
        self.assertIsInstance(payload['results'], list)
        self.assertEqual(payload['totals']['total'], 1)

    def test_a_row_reports_the_lineage_the_detail_route_expands(self):
        """A row identifies its plant well enough to act on or open."""
        plant = self.make_plant()
        row = self.page()['results'][0]
        batch = plant.cell_planting.seed_tray_planting.batch
        self.assertEqual(row['pk'], plant.pk)
        self.assertEqual(row['batch'], batch.pk)
        self.assertEqual(row['batch_code'], batch.code)
        self.assertEqual(row['variety'], batch.variety_id)
        self.assertEqual(row['variety_name'], batch.variety.name)
        self.assertEqual(row['lifecycle_state'], LifecycleState.GROWING)
        self.assertEqual(row['age_days'], 40)

    def test_the_row_carries_exactly_the_fields_the_screen_reads(self):
        """This repository has no JavaScript test runner, so the contract that
        `frontend/js/types/plantings.ts` describes is pinned here instead. A
        field renamed on either side fails this rather than rendering as
        undefined.
        """
        self.make_plant()
        self.assertEqual(sorted(self.page()['results'][0]), [
            'age_days',
            'batch',
            'batch_code',
            'cost',
            'currency_code',
            'expected_ready_early',
            'expected_ready_late',
            'final_outcome',
            'final_outcome_at',
            'garden_square',
            'germinated',
            'lifecycle_state',
            'located_since',
            'location',
            'location_label',
            'location_type',
            'pk',
            'plant_name',
            'seed_tray',
            'seed_tray_cell',
            'sellable',
            'standing_at',
            'standing_at_label',
            'variety',
            'variety_name',
        ])

    def test_a_garden_workspace_cannot_reach_the_register(self):
        """The Garden profile has plants but no nursery to run them through."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        for url in (self.url, self.ids_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_another_workspace_s_plants_are_invisible(self):
        """The register is scoped by the same boundary as every other read."""
        other = Workspace.objects.create(name='Other nursery')
        variety = make_plant_variety(workspace=other)
        foreign_batch = make_production_batch(workspace=other, variety=variety)
        self.assertEqual(self.page()['count'], 0)
        self.assertEqual(self.page()['totals']['total'], 0)
        self.assertEqual(foreign_batch.workspace_id, other.pk)


class RegisterFilterTests(RegisterTestCase):
    """Every filter narrows the rows and the totals by the same amount."""

    def assert_filter_agrees(self, **params):
        """Assert a filtered page's own total matches the rows it returns."""
        payload = self.page(**params, page_size=200)
        self.assertEqual(payload['totals']['total'], payload['count'])
        self.assertEqual(len(payload['results']), payload['count'])
        return payload

    def test_filtering_by_lifecycle_state_moves_rows_and_totals_together(self):
        """A count that outlived its filter would be worse than no count."""
        self.make_plant_in_state(LifecycleState.GROWING)
        available = self.make_plant_in_state(LifecycleState.AVAILABLE)
        self.make_plant_in_state(LifecycleState.FAILED)

        everything = self.page()
        self.assertEqual(everything['totals']['total'], 3)
        self.assertEqual(everything['totals']['available'], 1)
        self.assertEqual(everything['totals']['unresolved'], 2)

        payload = self.assert_filter_agrees(state=LifecycleState.AVAILABLE)
        self.assertEqual(self.row_ids(payload), [available.pk])

    def test_several_states_may_be_selected_at_once(self):
        """An operator asks for what is live, not for one state at a time."""
        growing = self.make_plant_in_state(LifecycleState.GROWING)
        available = self.make_plant_in_state(LifecycleState.AVAILABLE)
        self.make_plant_in_state(LifecycleState.CULLED)
        response = self.client.get(
            self.url,
            {'state': [LifecycleState.GROWING, LifecycleState.AVAILABLE]},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            sorted(row['pk'] for row in response.data['results']),
            sorted([growing.pk, available.pk]),
        )
        self.assertEqual(response.data['totals']['total'], 2)

    def test_sellable_selects_only_what_may_be_offered(self):
        """Growing stock is not available to promise, and must not be counted."""
        available = self.make_plant_in_state(LifecycleState.AVAILABLE)
        self.make_plant_in_state(LifecycleState.GROWING)
        self.make_plant_in_state(LifecycleState.RETAINED)
        payload = self.assert_filter_agrees(sellable='true')
        self.assertEqual(self.row_ids(payload), [available.pk])

    def test_filtering_by_variety_and_batch_narrows_the_selection(self):
        """The two identities a nursery plans by are both first-class filters."""
        packet = make_seed_packet()
        batch = self.make_batch(variety=packet.seeds.plant_variety)
        wanted = self.make_plant(batch=batch)
        self.make_plant()
        by_batch = self.assert_filter_agrees(batch=batch.pk)
        self.assertEqual(self.row_ids(by_batch), [wanted.pk])
        by_variety = self.assert_filter_agrees(variety=batch.variety_id)
        self.assertEqual(self.row_ids(by_variety), [wanted.pk])

    def test_age_filters_select_by_when_a_plant_germinated(self):
        """How old stock is drives what is worth chasing and what is late."""
        old = self.make_plant(germinated=timezone.now() - timedelta(days=60))
        young = self.make_plant(germinated=timezone.now() - timedelta(days=2))
        cutoff = (timezone.now() - timedelta(days=30)).isoformat()
        older = self.assert_filter_agrees(germinated_to=cutoff)
        self.assertEqual(self.row_ids(older), [old.pk])
        newer = self.assert_filter_agrees(germinated_from=cutoff)
        self.assertEqual(self.row_ids(newer), [young.pk])

    def test_location_filters_separate_trays_gardens_and_nowhere(self):
        """Where stock is standing is the question that starts a nursery day."""
        tray = make_seed_tray()
        in_tray = self.make_plant(tray=tray)
        make_specific_plant_location(specific_plant=in_tray, started=self.start)

        square = make_garden_square()
        planted_out = self.make_plant()
        make_specific_plant_location(
            specific_plant=planted_out,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            seed_tray_cell=None,
            garden_square=square,
            started=self.start,
        )
        unplaced = self.make_plant()

        in_trays = self.assert_filter_agrees(
            location_type=SpecificPlantLocation.SEED_TRAY_CELL,
        )
        self.assertEqual(self.row_ids(in_trays), [in_tray.pk])
        self.assertEqual(in_trays['results'][0]['seed_tray'], tray.pk)

        in_gardens = self.assert_filter_agrees(garden_square=square.pk)
        self.assertEqual(self.row_ids(in_gardens), [planted_out.pk])

        nowhere = self.assert_filter_agrees(location_type='none')
        self.assertEqual(self.row_ids(nowhere), [unplaced.pk])

    def test_a_generation_filter_selects_one_concrete_tray_fill(self):
        """A reused tray does not make its previous fill part of today's work."""
        generation = make_seed_tray_generation()
        wanted = self.make_plant(tray=generation.tray)
        self.make_plant()

        payload = self.assert_filter_agrees(generation=generation.pk)
        self.assertEqual(self.row_ids(payload), [wanted.pk])

    def test_a_finished_plant_leaves_its_location_behind(self):
        """An outcome that ends a location must stop reporting it as current."""
        plant = self.make_plant()
        make_specific_plant_location(specific_plant=plant, started=self.start)
        record_lifecycle_event(
            plant,
            self.operator,
            OutcomeRequest(EventType.CULLED, occurred_at=self.start + timedelta(days=1)),
        )
        row = self.page()['results'][0]
        self.assertIsNone(row['location_type'])
        self.assertEqual(self.page(location_type='none')['count'], 1)

    def test_search_matches_an_identifier_or_a_remembered_name(self):
        """An operator searches for the label in their hand or the crop's name."""
        packet = make_seed_packet()
        batch = self.make_batch(variety=packet.seeds.plant_variety, code='TOMATO-SPRING-1')
        wanted = self.make_plant(batch=batch)
        other = make_seed_packet()
        self.make_plant(batch=self.make_batch(
            variety=other.seeds.plant_variety,
            code='BASIL-SUMMER',
        ))
        by_code = self.assert_filter_agrees(search='tomato-spring')
        self.assertEqual(self.row_ids(by_code), [wanted.pk])
        by_id = self.assert_filter_agrees(search=str(wanted.pk))
        self.assertEqual(self.row_ids(by_id), [wanted.pk])
        by_variety = self.assert_filter_agrees(search=batch.variety.name)
        self.assertEqual(self.row_ids(by_variety), [wanted.pk])

    def test_combined_filters_agree_with_their_own_totals(self):
        """Filters compose, and the counts compose with them."""
        packet = make_seed_packet()
        batch = self.make_batch(variety=packet.seeds.plant_variety)
        wanted = self.make_plant_in_state(LifecycleState.AVAILABLE, batch=batch)
        self.make_plant_in_state(LifecycleState.GROWING, batch=batch)
        self.make_plant_in_state(LifecycleState.AVAILABLE)
        payload = self.assert_filter_agrees(
            batch=batch.pk,
            state=LifecycleState.AVAILABLE,
        )
        self.assertEqual(self.row_ids(payload), [wanted.pk])
        self.assertEqual(payload['totals']['available'], 1)

    def test_a_plant_standing_on_a_bench_is_found_under_it(self):
        """A potted plant set down on a bench is standing on that bench."""
        bench = make_location(location_type=Location.LocationType.BENCH)
        standing = self.make_plant()
        make_specific_plant_location(
            specific_plant=standing,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=bench,
            started=self.start,
        )
        self.make_plant()

        payload = self.assert_filter_agrees(location=bench.pk)
        self.assertEqual(self.row_ids(payload), [standing.pk])

    def test_a_plant_in_a_tray_is_found_under_the_bench_the_tray_stands_on(self):
        """The register answers what is standing there, not what is in a tray."""
        bench = make_location(location_type=Location.LocationType.BENCH)
        tray = make_seed_tray()
        tray.inventory_unit.current_location = bench
        tray.inventory_unit.save()
        in_tray = self.make_plant(tray=tray)
        make_specific_plant_location(
            specific_plant=in_tray,
            seed_tray_cell=in_tray.cell_planting.cell,
            started=self.start,
        )
        self.make_plant()

        payload = self.assert_filter_agrees(location=bench.pk)
        self.assertEqual(self.row_ids(payload), [in_tray.pk])
        self.assertEqual(payload['results'][0]['standing_at'], bench.pk)

    def test_a_greenhouse_answers_for_the_bays_inside_it(self):
        """Someone in the doorway does not think of its own bays as elsewhere."""
        greenhouse = make_location(location_type=Location.LocationType.GREENHOUSE)
        bay = make_location(
            location_type=Location.LocationType.BAY,
            parent=greenhouse,
        )
        deep = self.make_plant()
        make_specific_plant_location(
            specific_plant=deep,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=bay,
            started=self.start,
        )

        payload = self.assert_filter_agrees(location=greenhouse.pk)
        self.assertEqual(self.row_ids(payload), [deep.pk])

    def test_an_unknown_location_selects_nothing_rather_than_everything(self):
        """An empty path prefix would match the whole register."""
        self.make_plant()
        payload = self.assert_filter_agrees(location=99999)
        self.assertEqual(self.row_ids(payload), [])

    def test_another_workspace_s_location_selects_nothing(self):
        """A location filter is scoped like every other read."""
        other = Workspace.objects.create(name='Other nursery')
        outsider = make_location(workspace=other)
        self.make_plant()

        payload = self.assert_filter_agrees(location=outsider.pk)
        self.assertEqual(self.row_ids(payload), [])

    def test_bad_filter_values_blame_the_parameter_that_carried_them(self):
        """A typo in a query string should say which one it was."""
        for params, field in (
            ({'variety': 'tomato'}, 'variety'),
            ({'state': 'sprouting'}, 'state'),
            ({'sellable': 'maybe'}, 'sellable'),
            ({'germinated_from': 'last week'}, 'germinated_from'),
            ({'location_type': 'wheelbarrow'}, 'location_type'),
            ({'ordering': 'whim'}, 'ordering'),
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)


class RegisterPaginationTests(RegisterTestCase):
    """Paging moves the rows without moving what the totals describe."""

    def test_the_totals_are_the_same_on_every_page(self):
        """The counts describe the filter; only the rows describe the page."""
        for _ in range(5):
            self.make_plant_in_state(LifecycleState.AVAILABLE)
        first = self.page(page_size=2)
        second = self.page(page_size=2, page=2)
        self.assertEqual(len(first['results']), 2)
        self.assertEqual(first['totals'], second['totals'])
        self.assertEqual(first['totals']['total'], 5)
        self.assertEqual(first['totals']['available'], 5)

    def test_paging_visits_every_plant_exactly_once(self):
        """Equal sort values must not repeat or skip a plant across a boundary."""
        germinated = timezone.now() - timedelta(days=10)
        expected = {self.make_plant(germinated=germinated).pk for _ in range(7)}
        seen = []
        for page in (1, 2, 3, 4):
            seen.extend(self.row_ids(self.page(page_size=2, page=page)))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), expected)

    def test_the_page_size_is_capped(self):
        """A client cannot ask the register for the whole nursery at once."""
        for _ in range(3):
            self.make_plant()
        payload = self.page(page_size=10000)
        self.assertLessEqual(len(payload['results']), 200)


class RegisterOrderingTests(RegisterTestCase):
    """Sorting answers a question rather than reordering rows arbitrarily."""

    def test_plants_sort_by_age_newest_first_by_default(self):
        """The newest germinations are what an operator has just recorded."""
        old = self.make_plant(germinated=timezone.now() - timedelta(days=30))
        new = self.make_plant(germinated=timezone.now() - timedelta(days=1))
        self.assertEqual(self.row_ids(self.page()), [new.pk, old.pk])
        self.assertEqual(self.row_ids(self.page(ordering='age')), [old.pk, new.pk])

    def test_plants_sort_by_variety_name(self):
        """Sorting by crop groups the register the way a bench is walked."""
        first = self.make_plant(batch=self.make_batch(
            variety=make_plant_variety(name='Aubergine'),
        ))
        last = self.make_plant(batch=self.make_batch(
            variety=make_plant_variety(name='Zucchini'),
        ))
        self.assertEqual(self.row_ids(self.page(ordering='variety')), [first.pk, last.pk])
        self.assertEqual(self.row_ids(self.page(ordering='-variety')), [last.pk, first.pk])


class RegisterSelectionTests(RegisterTestCase):
    """A bulk selection is a filter, resolved when it is acted on."""

    def test_the_ids_action_returns_what_the_same_filters_select(self):
        """Bulk actions and the list they came from must agree exactly."""
        available = self.make_plant_in_state(LifecycleState.AVAILABLE)
        self.make_plant_in_state(LifecycleState.GROWING)
        response = self.client.get(self.ids_url, {'state': LifecycleState.AVAILABLE})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'count': 1, 'plants': [available.pk]})

    def test_a_selection_larger_than_the_cap_is_refused(self):
        """A selection too big to state is a filter nobody finished writing."""
        for _ in range(3):
            self.make_plant()
        with mock.patch.object(register_rest, 'MAX_SELECTION', 2):
            response = self.client.get(self.ids_url)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('detail', response.data)

    def test_a_selection_inside_the_cap_is_returned_whole(self):
        """The cap bounds the answer without truncating a legitimate one."""
        plants = {self.make_plant().pk for _ in range(3)}
        with mock.patch.object(register_rest, 'MAX_SELECTION', 3):
            response = self.client.get(self.ids_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data['plants']), plants)


class RegisterQueryBudgetTests(RegisterTestCase):
    """The register's cost must not grow with the size of its answer."""

    def test_a_page_costs_the_same_number_of_queries_at_any_volume(self):
        """A register that queried per row could not serve a real nursery."""
        packet = make_seed_packet()
        batch = self.make_batch(variety=packet.seeds.plant_variety)
        tray = make_seed_tray(model=make_seed_tray_model(x_cells=10, y_cells=10))
        planting = make_seed_tray_planting(batch=batch, seeds_used=packet, seed_tray=tray)
        for index in range(60):
            cell_planting = make_seed_tray_cell_planting(
                seed_tray_planting=planting,
                cell=make_seed_tray_cell(
                    tray=tray,
                    x_position=index % 10,
                    y_position=index // 10,
                ),
            )
            plant = make_specific_plant(cell_planting=cell_planting, germinated=self.start)
            record_germination_event(plant, self.operator)

        with CaptureQueriesContext(connection) as small_queries:
            small = self.client.get(self.url, {'page_size': 5})
        with CaptureQueriesContext(connection) as large_queries:
            large = self.client.get(self.url, {'page_size': 60})

        self.assertEqual(len(small.data['results']), 5)
        self.assertEqual(len(large.data['results']), 60)
        self.assertEqual(len(large_queries), len(small_queries))
        self.assertLessEqual(len(small_queries), 6, small_queries.captured_queries)
