"""Tests for the harvest REST contract and the yield report endpoint."""
# pylint: disable=duplicate-code
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from inventory.units import UnitCode
from tests.api import RESTContractTestCase
from tests.factories import (
    make_garden_row,
    make_garden_square,
    make_harvest,
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_production_batch,
    make_seed_tray_cell_planting,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .lifecycle import record_germination_event
from .models import Harvest, PlantLifecycleEvent


class HarvestRESTTestCase(RESTContractTestCase):
    """Shared fixtures and helpers for the harvest API tests."""

    url = '/plantings/harvests/'
    report_url = '/plantings/harvest-report/'

    def setUp(self):
        super().setUp()
        self.batch = make_production_batch()
        self.square = make_garden_square()

    def payload(self, **overrides):
        """Build a valid create payload for the fixture batch."""
        values = {
            'batch': self.batch.pk,
            'harvested_at': timezone.now().isoformat(),
            'quantity': '1.500000000',
            'unit_code': UnitCode.KILOGRAM,
        }
        values.update(overrides)
        return values

    def create(self, **overrides):
        """Post one harvest and assert it was accepted."""
        response = self.client.post(self.url, self.payload(**overrides), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data


class HarvestContractTests(HarvestRESTTestCase):
    """The collection follows the common contract and refuses edits."""

    def test_list_route_requires_authentication_and_returns_a_list(self):
        """Harvests follow the common authenticated collection contract."""
        self.assert_authentication_required([self.url, self.report_url])
        self.assert_list_contract([self.url])

    def test_create_and_retrieve_round_trip(self):
        """A created harvest reads back through its detail route."""
        self.assert_create_retrieve(
            self.url,
            self.payload(),
            expected_fields={
                'batch': self.batch.pk,
                'quantity': '1.500000000',
                'unit_code': UnitCode.KILOGRAM,
            },
        )

    def test_a_created_harvest_is_already_posted(self):
        """There is no draft step; recording a harvest counts it."""
        created = self.create()
        self.assertEqual(created['status'], Harvest.Status.POSTED)
        self.assertIsNotNone(created['posted_at'])
        self.assertIsNone(created['reversed_at'])
        self.assertEqual(created['plants'], [])
        self.assertEqual(created['finished_plants'], [])

    def test_the_quantity_is_a_string_with_a_stable_unit_code(self):
        """Decimal yield never round-trips through a float."""
        created = self.create(quantity='0.125000000', unit_code=UnitCode.LITRE)
        self.assertIsInstance(created['quantity'], str)
        self.assertEqual(Decimal(created['quantity']), Decimal('0.125'))
        self.assertEqual(created['unit_code'], UnitCode.LITRE)

    def test_a_posted_harvest_cannot_be_edited_or_deleted(self):
        """Generic writes are not offered; a correction reverses instead."""
        created = self.create()
        detail = f'{self.url}{created["pk"]}/'
        for method, kwargs in (
            ('put', {'data': self.payload(), 'format': 'json'}),
            ('patch', {'data': {'notes': 'edited'}, 'format': 'json'}),
            ('delete', {}),
        ):
            with self.subTest(method=method):
                response = getattr(self.client, method)(detail, **kwargs)
                self.assertEqual(response.status_code, 405, response.data)

    def test_the_location_label_names_where_a_harvest_came_from(self):
        """A square or a row reads back with a human label."""
        created = self.create(garden_square=self.square.pk)
        self.assertEqual(created['garden_square'], self.square.pk)
        self.assertEqual(created['location_label'], str(self.square))


class HarvestValidationTests(HarvestRESTTestCase):
    """Invalid harvests are refused with field-level errors."""

    def _reject(self, expected_field, **overrides):
        """Post an invalid payload and assert which field was blamed."""
        response = self.client.post(self.url, self.payload(**overrides), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(expected_field, response.data)
        return response.data

    def test_zero_and_negative_quantities_are_rejected(self):
        """A harvest that measured nothing or less is not an observation."""
        for quantity in ('0', '-5'):
            with self.subTest(quantity=quantity):
                self._reject('quantity', quantity=quantity)

    def test_input_and_area_units_are_rejected(self):
        """Only count, mass, and volume describe a crop that came out."""
        for unit in (UnitCode.SEED, UnitCode.SEED_CLUSTER, UnitCode.SQUARE_METRE):
            with self.subTest(unit=unit):
                self._reject('unit_code', unit_code=unit)

    def test_a_future_harvest_is_rejected(self):
        """A crop cannot be picked before it exists."""
        ahead = (timezone.now() + timedelta(days=1)).isoformat()
        self._reject('harvested_at', harvested_at=ahead)

    def test_a_square_and_a_row_cannot_both_be_recorded(self):
        """One harvest came from one place."""
        self._reject(
            'garden_row',
            garden_square=self.square.pk,
            garden_row=make_garden_row().pk,
        )

    def test_finishing_without_a_selection_is_rejected(self):
        """There is nothing to finish when no plant was named."""
        self._reject('plants', finish_plants=True)

    def test_a_planned_batch_cannot_be_harvested(self):
        """Nothing has been sown yet, so nothing can have come out."""
        from .models import ProductionBatch  # pylint: disable=import-outside-toplevel
        batch = make_production_batch(status=ProductionBatch.Status.PLANNED)
        self._reject('batch', batch=batch.pk)


class HarvestWorkspaceScopingTests(HarvestRESTTestCase):
    """References outside this workspace are field errors, not 404s."""

    def setUp(self):
        super().setUp()
        self.other = Workspace.objects.create(name='Other workspace')

    def _foreign_batch(self):
        """Create an active batch belonging to the other workspace."""
        family = make_plant_family(workspace=self.other)
        plant = make_plant(workspace=self.other, family=family)
        variety = make_plant_variety(workspace=self.other, plant=plant)
        return make_production_batch(workspace=self.other, variety=variety)

    def test_a_foreign_batch_is_a_field_error(self):
        """Scoping is enforced by narrowing the field, not by hiding the row."""
        response = self.client.post(
            self.url,
            self.payload(batch=self._foreign_batch().pk),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('batch', response.data)

    def test_a_foreign_square_or_row_is_a_field_error(self):
        """A growing location is scoped like every other reference."""
        for field, value in (
            ('garden_square', make_garden_square(workspace=self.other).pk),
            ('garden_row', make_garden_row(workspace=self.other).pk),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    self.url,
                    self.payload(**{field: value}),
                    format='json',
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)

    def test_foreign_harvests_are_hidden_from_the_list_and_detail(self):
        """Another workspace's yield is not this workspace's business."""
        foreign = make_harvest(
            workspace=self.other,
            batch=self._foreign_batch(),
        )
        listed = self.client.get(self.url)
        self.assertEqual([row['pk'] for row in listed.data], [])
        detail = self.client.get(f'{self.url}{foreign.pk}/')
        self.assertEqual(detail.status_code, 404)


class HarvestPlantSelectionRESTTests(HarvestRESTTestCase):
    """Attribution and final harvests are validated at the API boundary."""

    def setUp(self):
        super().setUp()
        cell_planting = make_seed_tray_cell_planting()
        self.batch = cell_planting.seed_tray_planting.batch
        self.plant = make_specific_plant(cell_planting=cell_planting)
        record_germination_event(self.plant, self.user)
        make_specific_plant_location(specific_plant=self.plant)

    def test_unknown_plant_ids_are_listed_rather_than_hidden(self):
        """A caller is told which IDs were wrong instead of getting a 404."""
        response = self.client.post(
            self.url,
            self.payload(plants=[999999], unit_code=UnitCode.EACH, quantity='2'),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('plants', response.data)
        self.assertIn('999999', str(response.data['plants']))

    def test_plants_from_another_batch_are_rejected(self):
        """A harvest cannot be attributed to a crop it did not come from."""
        stranger = make_specific_plant()
        response = self.client.post(
            self.url,
            self.payload(plants=[stranger.pk], unit_code=UnitCode.EACH, quantity='2'),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('plants', response.data)

    def test_a_harvest_can_leave_its_plants_growing(self):
        """Most crops are picked repeatedly, so picking ends nothing by default."""
        created = self.create(
            plants=[self.plant.pk],
            unit_code=UnitCode.EACH,
            quantity='3',
        )
        self.assertEqual(created['plants'], [self.plant.pk])
        self.assertEqual(created['finished_plants'], [])
        self.assertFalse(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=PlantLifecycleEvent.EventType.HARVEST_FINISHED,
            ).exists()
        )

    def test_a_final_harvest_reports_the_plants_it_ended(self):
        """The response says whether the harvest resolved anything."""
        created = self.create(
            plants=[self.plant.pk],
            unit_code=UnitCode.EACH,
            quantity='3',
            finish_plants=True,
            finish_reason='Pulled at the end of the season.',
        )
        self.assertEqual(created['finished_plants'], [self.plant.pk])
        self.assertEqual(
            PlantLifecycleEvent.objects.filter(
                plant=self.plant,
                event_type=PlantLifecycleEvent.EventType.HARVEST_FINISHED,
                reference=f'harvest:{created["pk"]}',
            ).count(),
            1,
        )

    def test_finishing_an_already_finished_plant_reports_the_plant(self):
        """The error names the plant that could not accept the outcome."""
        self.create(
            plants=[self.plant.pk],
            unit_code=UnitCode.EACH,
            quantity='3',
            finish_plants=True,
        )
        response = self.client.post(
            self.url,
            self.payload(
                plants=[self.plant.pk],
                unit_code=UnitCode.EACH,
                quantity='1',
                finish_plants=True,
            ),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('plants', response.data)
        self.assertIn(str(self.plant.pk), str(response.data['plants']))
        self.assertEqual(Harvest.objects.count(), 1)


class HarvestReversalRESTTests(HarvestRESTTestCase):
    """Reversal is the only way a posted harvest stops counting."""

    def setUp(self):
        super().setUp()
        self.harvest_pk = self.create()['pk']

    def _reverse(self, **payload):
        """Post the reverse action for the fixture harvest."""
        return self.client.post(
            f'{self.url}{self.harvest_pk}/reverse/',
            payload,
            format='json',
        )

    def test_reversing_keeps_the_record_and_drops_it_from_the_report(self):
        """The audit trail survives while the totals forget it."""
        response = self._reverse(reason='Weighed the wrong crate.')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], Harvest.Status.REVERSED)
        self.assertEqual(response.data['reverse_reason'], 'Weighed the wrong crate.')

        listed = self.client.get(self.url)
        self.assertEqual([row['pk'] for row in listed.data], [self.harvest_pk])

        report = self.client.get(self.report_url, {'group_by': 'batch'})
        self.assertEqual(report.data, [])

    def test_a_reversal_requires_a_reason(self):
        """An unexplained correction is not an audit record."""
        for reason in ('', '   '):
            with self.subTest(reason=repr(reason)):
                response = self._reverse(reason=reason)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('reason', response.data)

    def test_a_harvest_cannot_be_reversed_twice(self):
        """One correction is enough."""
        self._reverse(reason='Weighed the wrong crate.')
        response = self._reverse(reason='Again.')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('status', response.data)


class HarvestFilterTests(HarvestRESTTestCase):
    """The collection narrows by crop, place, status, and period."""

    def setUp(self):
        super().setUp()
        self.other_batch = make_production_batch()
        self.here = self.create(garden_square=self.square.pk)['pk']
        self.elsewhere = self.create(batch=self.other_batch.pk)['pk']

    def _pks(self, **params):
        """Return the harvest IDs one filtered list returns."""
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [row['pk'] for row in response.data]

    def test_filtering_by_batch_variety_and_square(self):
        """Each filter narrows to the harvests that match it."""
        self.assertEqual(self._pks(batch=self.batch.pk), [self.here])
        self.assertEqual(
            self._pks(variety=self.other_batch.variety_id),
            [self.elsewhere],
        )
        self.assertEqual(self._pks(garden_square=self.square.pk), [self.here])

    def test_filtering_by_status(self):
        """A reversed harvest is still listable on its own."""
        self.client.post(
            f'{self.url}{self.here}/reverse/',
            {'reason': 'Mistake.'},
            format='json',
        )
        self.assertEqual(self._pks(status='reversed'), [self.here])
        self.assertEqual(self._pks(status='posted'), [self.elsewhere])

    def test_filtering_by_an_inclusive_local_day_range(self):
        """The from and to bounds both name whole local days."""
        today = timezone.localdate().isoformat()
        self.assertEqual(
            sorted(self._pks(harvested_from=today, harvested_to=today)),
            sorted([self.here, self.elsewhere]),
        )
        past = (timezone.localdate() - timedelta(days=400)).isoformat()
        self.assertEqual(self._pks(harvested_from=past, harvested_to=past), [])

    def test_malformed_filters_are_rejected(self):
        """A typo is reported rather than silently ignored."""
        for params, field in (
            ({'batch': 'abc'}, 'batch'),
            ({'plant': 'abc'}, 'plant'),
            ({'status': 'draft'}, 'status'),
            ({'harvested_from': 'yesterday'}, 'harvested_from'),
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)


class HarvestReportRESTTests(HarvestRESTTestCase):
    """The report groups yield without ever combining unlike dimensions."""

    def setUp(self):
        super().setUp()
        self.create(garden_square=self.square.pk, quantity='1.5')
        self.create(garden_square=self.square.pk, quantity='250', unit_code=UnitCode.GRAM)
        self.create(quantity='12', unit_code=UnitCode.EACH)

    def _rows(self, **params):
        """Run the report and assert it was accepted."""
        response = self.client.get(self.report_url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_weight_harvests_total_by_variety(self):
        """Kilograms and grams combine exactly into one mass total."""
        rows = self._rows(group_by='variety')
        self.assertEqual(len(rows), 1)
        totals = {
            entry['conversion_family']: entry for entry in rows[0]['totals']
        }
        self.assertEqual(Decimal(totals['metric_mass']['quantity']), Decimal('1750'))
        self.assertEqual(Decimal(totals['each']['quantity']), Decimal('12'))

    def test_totals_group_by_square_and_leave_unlocated_yield_apart(self):
        """A harvest with no square is not attributed to one."""
        rows = {row['key']: row for row in self._rows(group_by='garden_square')}
        self.assertEqual(
            Decimal(rows[self.square.pk]['totals'][0]['quantity']),
            Decimal('1750'),
        )
        self.assertEqual(rows[None]['harvest_count'], 1)

    def test_a_date_range_narrows_the_report(self):
        """A season is expressed as a range rather than guessed."""
        today = timezone.localdate().isoformat()
        self.assertEqual(len(self._rows(group_by='batch', harvested_from=today)), 1)
        past = (timezone.localdate() - timedelta(days=400)).isoformat()
        self.assertEqual(self._rows(group_by='batch', harvested_to=past), [])

    def test_lineage_counts_are_reported_without_a_ratio(self):
        """Seeds, plants, and harvests stay three separate numbers."""
        row = self._rows(group_by='batch')[0]
        self.assertIn('seeds_sown', row)
        self.assertIn('plants_observed', row)
        self.assertIn('plants_harvest_finished', row)
        for key in row:
            self.assertNotIn('ratio', key)
            self.assertNotIn('per_', key)

    def test_an_unknown_grouping_is_rejected(self):
        """A typo is reported rather than silently defaulted."""
        response = self.client.get(self.report_url, {'group_by': 'weather'})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('group_by', response.data)
