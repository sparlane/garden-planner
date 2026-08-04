"""Tests for yield aggregation across recorded harvests."""
# pylint: disable=duplicate-code
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.units import UnitCode
from tests.factories import (
    make_garden_row,
    make_garden_square,
    make_harvest,
    make_production_batch,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_specific_plant,
)
from workspaces.models import get_current_workspace

from .harvests import HarvestRequest, record_harvest, reverse_harvest
from .lifecycle import record_germination_event
from .models import Harvest, HarvestPlant
from .yields import (
    GroupBy,
    NO_LOCATION_LABEL,
    batch_harvest_finished_count,
    batch_harvest_totals,
    harvest_report,
)


def _report(group_by, **filters):
    """Run a report over the current workspace with one grouping."""
    return harvest_report(
        get_current_workspace(),
        {'group_by': group_by, **filters},
    )


def _total(row, family):
    """Return one family's total from a report row, or None if absent."""
    for entry in row['totals']:
        if entry['conversion_family'] == family:
            return entry
    return None


class UnitFamilyTotalTests(TestCase):
    """Compatible units combine exactly; incompatible ones never do."""

    def setUp(self):
        super().setUp()
        self.batch = make_production_batch()

    def test_grams_and_kilograms_total_exactly_in_grams(self):
        """Converting into a family's reference unit cannot lose precision."""
        make_harvest(batch=self.batch, quantity=Decimal('1.5'), unit_code=UnitCode.KILOGRAM)
        make_harvest(batch=self.batch, quantity=Decimal('250'), unit_code=UnitCode.GRAM)
        mass = _total(_report(GroupBy.BATCH)[0], 'metric_mass')
        self.assertEqual(mass['unit_code'], UnitCode.GRAM)
        self.assertEqual(Decimal(mass['quantity']), Decimal('1750'))

    def test_millilitres_and_litres_total_exactly_in_millilitres(self):
        """Volume behaves the same way mass does."""
        make_harvest(batch=self.batch, quantity=Decimal('2'), unit_code=UnitCode.LITRE)
        make_harvest(batch=self.batch, quantity=Decimal('125'), unit_code=UnitCode.MILLILITRE)
        volume = _total(_report(GroupBy.BATCH)[0], 'metric_volume')
        self.assertEqual(volume['unit_code'], UnitCode.MILLILITRE)
        self.assertEqual(Decimal(volume['quantity']), Decimal('2125'))

    def test_count_and_weight_stay_in_separate_totals(self):
        """Forty fruit plus twelve kilograms is not a number."""
        make_harvest(batch=self.batch, quantity=Decimal('40'), unit_code=UnitCode.EACH)
        make_harvest(batch=self.batch, quantity=Decimal('12'), unit_code=UnitCode.KILOGRAM)
        row = _report(GroupBy.BATCH)[0]
        self.assertEqual(len(row['totals']), 2)
        self.assertEqual(Decimal(_total(row, 'each')['quantity']), Decimal('40'))
        self.assertEqual(Decimal(_total(row, 'metric_mass')['quantity']), Decimal('12000'))
        self.assertEqual(row['harvest_count'], 2)

    def test_a_batch_total_matches_the_report(self):
        """The batch screen and the report share one derivation."""
        make_harvest(batch=self.batch, quantity=Decimal('3'), unit_code=UnitCode.KILOGRAM)
        self.assertEqual(batch_harvest_totals(self.batch), _report(GroupBy.BATCH)[0]['totals'])

    def test_a_reversed_harvest_is_excluded_from_totals(self):
        """A retracted harvest stops counting the moment it is reversed."""
        user = get_user_model().objects.create_user(username='yield-reverser')
        make_harvest(batch=self.batch, quantity=Decimal('1'), unit_code=UnitCode.KILOGRAM)
        drop = make_harvest(batch=self.batch, quantity=Decimal('9'), unit_code=UnitCode.KILOGRAM)
        reverse_harvest(drop, user, 'Weighed the wrong crate.')
        row = _report(GroupBy.BATCH)[0]
        self.assertEqual(Decimal(_total(row, 'metric_mass')['quantity']), Decimal('1000'))
        self.assertEqual(row['harvest_count'], 1)
        self.assertEqual(Harvest.objects.count(), 2)


class GroupingTests(TestCase):
    """Every supported dimension groups the same harvests differently."""

    def setUp(self):
        super().setUp()
        self.square = make_garden_square()
        self.row = make_garden_row()
        self.batch = make_production_batch()
        self.other_batch = make_production_batch()
        make_harvest(
            batch=self.batch,
            garden_square=self.square,
            quantity=Decimal('1'),
            unit_code=UnitCode.KILOGRAM,
        )
        make_harvest(
            batch=self.other_batch,
            garden_row=self.row,
            quantity=Decimal('2'),
            unit_code=UnitCode.KILOGRAM,
        )
        make_harvest(batch=self.batch, quantity=Decimal('4'), unit_code=UnitCode.KILOGRAM)

    def test_grouping_by_batch_separates_the_crops(self):
        """Each batch reports only what it produced."""
        rows = {row['key']: row for row in _report(GroupBy.BATCH)}
        self.assertEqual(
            Decimal(_total(rows[self.batch.pk], 'metric_mass')['quantity']),
            Decimal('5000'),
        )
        self.assertEqual(
            Decimal(_total(rows[self.other_batch.pk], 'metric_mass')['quantity']),
            Decimal('2000'),
        )

    def test_grouping_by_variety_labels_the_crop(self):
        """A variety row names the plant and the variety it belongs to."""
        rows = _report(GroupBy.VARIETY)
        self.assertEqual(len(rows), 2)
        labels = {row['label'] for row in rows}
        self.assertIn(
            f'{self.batch.variety.plant.name} — {self.batch.variety.name}',
            labels,
        )

    def test_grouping_by_square_collects_unlocated_harvests_separately(self):
        """A harvest with no location is not attributed to somewhere it wasn't."""
        rows = {row['key']: row for row in _report(GroupBy.GARDEN_SQUARE)}
        self.assertEqual(
            Decimal(_total(rows[self.square.pk], 'metric_mass')['quantity']),
            Decimal('1000'),
        )
        self.assertEqual(rows[None]['label'], NO_LOCATION_LABEL)
        self.assertEqual(rows[None]['harvest_count'], 2)

    def test_grouping_by_row_reports_the_row_that_grew_it(self):
        """Direct-sown rows are a growing location like squares are."""
        rows = {row['key']: row for row in _report(GroupBy.GARDEN_ROW)}
        self.assertEqual(
            Decimal(_total(rows[self.row.pk], 'metric_mass')['quantity']),
            Decimal('2000'),
        )

    def test_grouping_by_year_buckets_the_calendar(self):
        """A season is a date range; the calendar axis inside it is by period."""
        rows = _report(GroupBy.YEAR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['key'], f'{timezone.localdate().year:04d}')
        self.assertEqual(rows[0]['harvest_count'], 3)

    def test_a_date_range_narrows_the_report(self):
        """The from and to bounds are both inclusive local days."""
        today = timezone.localdate()
        rows = _report(GroupBy.BATCH, harvested_from=today, harvested_to=today)
        self.assertEqual(sum(row['harvest_count'] for row in rows), 3)
        past = today - timedelta(days=400)
        self.assertEqual(
            _report(GroupBy.BATCH, harvested_from=past, harvested_to=past),
            [],
        )


class PlantAttributionTests(TestCase):
    """A per-plant report never triples or invents a shared measurement."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='attributor')
        cell_planting = make_seed_tray_cell_planting()
        self.batch = cell_planting.seed_tray_planting.batch
        self.plants = [
            make_specific_plant(cell_planting=cell_planting) for _ in range(3)
        ]
        for plant in self.plants:
            record_germination_event(plant, self.user)

    def _record(self, quantity, plants):
        """Post one count harvest attributed to the given plants."""
        return record_harvest(self.batch.workspace, self.user, HarvestRequest(
            batch=self.batch,
            harvested_at=timezone.now(),
            quantity=quantity,
            unit_code=UnitCode.EACH,
            plant_ids=tuple(plant.pk for plant in plants),
        ))

    def test_a_solely_attributed_harvest_is_that_plant_s_own_yield(self):
        """One plant, one measurement, one total."""
        self._record(Decimal('5'), [self.plants[0]])
        rows = {row['key']: row for row in _report(GroupBy.PLANT)}
        row = rows[self.plants[0].pk]
        self.assertEqual(Decimal(_total(row, 'each')['quantity']), Decimal('5'))
        self.assertEqual(row['shared_totals'], [])

    def test_a_shared_harvest_is_reported_beside_the_total_not_inside_it(self):
        """Three plants sharing one crate did not each grow the whole crate."""
        self._record(Decimal('9'), self.plants)
        rows = {row['key']: row for row in _report(GroupBy.PLANT)}
        for plant in self.plants:
            row = rows[plant.pk]
            self.assertEqual(row['totals'], [])
            self.assertEqual(len(row['shared_totals']), 1)
            self.assertEqual(
                Decimal(row['shared_totals'][0]['quantity']),
                Decimal('9'),
            )

    def test_other_groupings_never_report_shared_totals(self):
        """The split only means anything when the grouping is a plant."""
        self._record(Decimal('9'), self.plants)
        for group_by in (GroupBy.BATCH, GroupBy.VARIETY, GroupBy.MONTH):
            with self.subTest(group_by=group_by):
                self.assertEqual(_report(group_by)[0]['shared_totals'], [])

    def test_a_shared_harvest_is_counted_once_per_batch(self):
        """Attribution must not multiply the crop the batch actually produced."""
        self._record(Decimal('9'), self.plants)
        self.assertEqual(HarvestPlant.objects.count(), 3)
        row = _report(GroupBy.BATCH)[0]
        self.assertEqual(Decimal(_total(row, 'each')['quantity']), Decimal('9'))
        self.assertEqual(row['harvest_count'], 1)


class LineageCountTests(TestCase):
    """Seed, plant, and harvest counts are reported as separate integers."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='counter')
        sowing = make_seed_tray_planting(quantity=40)
        cell_planting = make_seed_tray_cell_planting(seed_tray_planting=sowing)
        self.batch = sowing.batch
        self.plants = [
            make_specific_plant(cell_planting=cell_planting) for _ in range(4)
        ]
        for plant in self.plants:
            record_germination_event(plant, self.user)
        record_harvest(self.batch.workspace, self.user, HarvestRequest(
            batch=self.batch,
            harvested_at=timezone.now(),
            quantity=Decimal('3'),
            unit_code=UnitCode.EACH,
            plant_ids=(self.plants[0].pk, self.plants[1].pk),
            finish_plants=True,
        ))

    def test_the_three_counts_are_reported_independently(self):
        """Sowing, germination, and harvest are three separate outcomes."""
        row = _report(GroupBy.BATCH)[0]
        self.assertEqual(row['seeds_sown'], 40)
        self.assertEqual(row['plants_observed'], 4)
        self.assertEqual(row['plants_harvest_finished'], 2)

    def test_no_ratio_of_unlike_things_is_reported(self):
        """Seeds sown divided by weight picked is not a rate of anything."""
        row = _report(GroupBy.BATCH)[0]
        for key in row:
            self.assertNotIn('ratio', key)
            self.assertNotIn('rate', key)
            self.assertNotIn('per_', key)

    def test_lineage_is_absent_where_it_is_undefined(self):
        """A square, a row, or a month may hold several crops at once."""
        for group_by in (GroupBy.GARDEN_SQUARE, GroupBy.GARDEN_ROW,
                         GroupBy.MONTH, GroupBy.YEAR, GroupBy.PLANT):
            with self.subTest(group_by=group_by):
                row = _report(group_by)[0]
                self.assertIsNone(row['seeds_sown'])
                self.assertIsNone(row['plants_observed'])
                self.assertIsNone(row['plants_harvest_finished'])

    def test_the_finished_count_matches_the_batch_helper(self):
        """The report and the batch screen share one derivation."""
        self.assertEqual(batch_harvest_finished_count(self.batch), 2)


class LocalCalendarTests(TestCase):
    """Buckets and day bounds are cut in the workspace's own timezone."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.save()
        self.zone = ZoneInfo('Pacific/Auckland')
        self.batch = make_production_batch()

    def _at(self, local):
        """Post a one-kilogram harvest at one local wall-clock time."""
        make_harvest(
            batch=self.batch,
            harvested_at=local.replace(tzinfo=self.zone),
            quantity=Decimal('1'),
            unit_code=UnitCode.KILOGRAM,
        )

    def test_a_late_evening_harvest_buckets_into_its_local_month(self):
        """23:30 on the last of the month is not the next month in UTC terms."""
        self._at(datetime(2026, 4, 30, 23, 30))
        rows = _report(GroupBy.MONTH)
        self.assertEqual([row['key'] for row in rows], ['2026-04'])

    def test_a_late_evening_harvest_falls_inside_its_local_day(self):
        """An inclusive to-bound covers the whole local day it names."""
        self._at(datetime(2026, 4, 30, 23, 30))
        rows = _report(
            GroupBy.BATCH,
            harvested_from=date(2026, 4, 30),
            harvested_to=date(2026, 4, 30),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['harvest_count'], 1)

    def test_the_next_local_day_is_excluded(self):
        """The bound is a local day, not a rounded UTC instant."""
        self._at(datetime(2026, 5, 1, 0, 30))
        self.assertEqual(
            _report(
                GroupBy.BATCH,
                harvested_from=date(2026, 4, 30),
                harvested_to=date(2026, 4, 30),
            ),
            [],
        )
