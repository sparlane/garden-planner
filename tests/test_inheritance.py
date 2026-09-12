"""What a variety is planned by, and whose figure each one is.

A blank cultivation figure on a variety is not a missing figure: ``plants.
metadata`` reads it through to the crop everywhere a sowing date, a maturity
date or a projected task is worked out. So the catalog answers the same way,
and says which half of the answer the variety wrote down itself -- because a
blank and a figure that happens to equal the crop's mean different things the
next time the crop is corrected.
"""

from plants.metadata import INHERITED_FIELDS, variety_days, variety_inheritance
from tests.api import RESTContractTestCase
from tests.factories import make_plant, make_plant_variety


CROP_FIGURES = {
    'spacing': 50,
    'inter_row_spacing': 300,
    'plants_per_square_foot': 16,
    'germination_days_min': 7,
    'germination_days_max': 21,
    'maturity_days_min': 70,
    'maturity_days_max': 90,
    'maturity_basis': 'transplanting',
}


class VarietyInheritanceTestCase(RESTContractTestCase):
    """One crop that carries every figure, and varieties under it."""

    def setUp(self):
        super().setUp()
        self.crop = make_plant(name='Carrot', **CROP_FIGURES)

    def effective(self, variety):
        """Return what the catalog says a variety is planned by."""
        response = self.client.get(f'/plants/variety/{variety.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['effective']


class InheritedFigureTests(VarietyInheritanceTestCase):
    """A variety that says nothing is planned by its crop."""

    def test_a_blank_figure_reports_the_crop_and_says_so(self):
        """Which is what every date is already worked out from."""
        variety = make_plant_variety(plant=self.crop, name='Nantes')

        effective = self.effective(variety)

        self.assertEqual(effective['spacing'], {'value': 50, 'inherited': True})
        self.assertEqual(
            effective['maturity_days_min'], {'value': 70, 'inherited': True},
        )

    def test_an_override_reports_the_variety_and_says_so(self):
        """What a variety writes down is what it is grown by."""
        variety = make_plant_variety(
            plant=self.crop, name='Nantes', spacing=40, maturity_days_min=65,
        )

        effective = self.effective(variety)

        self.assertEqual(effective['spacing'], {'value': 40, 'inherited': False})
        self.assertEqual(
            effective['maturity_days_min'], {'value': 65, 'inherited': False},
        )

    def test_every_inherited_figure_is_reported(self):
        """A screen reading one of them should not have to guess at the rest."""
        variety = make_plant_variety(plant=self.crop, name='Nantes')

        effective = self.effective(variety)

        self.assertEqual(set(effective), set(INHERITED_FIELDS))
        self.assertEqual(
            {name: entry['value'] for name, entry in effective.items()},
            CROP_FIGURES,
        )

    def test_an_override_equal_to_the_crop_is_still_the_varietys_own(self):
        """The two are only the same figure until the crop is corrected."""
        variety = make_plant_variety(plant=self.crop, name='Nantes', spacing=50)

        self.assertEqual(
            self.effective(variety)['spacing'], {'value': 50, 'inherited': False},
        )

        self.client.patch(
            f'/plants/plant/{self.crop.pk}/', {'spacing': 75}, format='json',
        )

        self.assertEqual(
            self.effective(variety)['spacing'], {'value': 50, 'inherited': False},
        )

    def test_correcting_the_crop_moves_every_variety_inheriting_from_it(self):
        """Which is the whole reason a figure is left blank."""
        variety = make_plant_variety(plant=self.crop, name='Nantes')

        self.client.patch(
            f'/plants/plant/{self.crop.pk}/', {'spacing': 75}, format='json',
        )

        self.assertEqual(
            self.effective(variety)['spacing'], {'value': 75, 'inherited': True},
        )

    def test_clearing_an_override_hands_the_figure_back_to_the_crop(self):
        """A correction that says this variety is ordinary after all."""
        variety = make_plant_variety(plant=self.crop, name='Nantes', spacing=40)

        response = self.client.patch(
            f'/plants/variety/{variety.pk}/', {'spacing': None}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data['effective']['spacing'], {'value': 50, 'inherited': True},
        )

    def test_what_the_catalog_says_is_what_the_dates_are_worked_out_from(self):
        """One resolution, so a screen and a due date cannot disagree."""
        variety = make_plant_variety(
            plant=self.crop, name='Nantes', maturity_days_min=65,
        )

        effective = variety_inheritance(variety)

        self.assertEqual(
            variety_days(variety, 'maturity'),
            (effective['maturity_days_min']['value'], effective['maturity_days_max']['value']),
        )
        self.assertEqual(
            variety_days(variety, 'germination'),
            (
                effective['germination_days_min']['value'],
                effective['germination_days_max']['value'],
            ),
        )


class CropVarietyFilterTests(VarietyInheritanceTestCase):
    """A crop's own varieties, out of a catalog too long to be sent whole."""

    def test_a_crop_asks_for_its_own_varieties(self):
        """Which is the one level of the catalog a screen cannot file itself."""
        mine = make_plant_variety(plant=self.crop, name='Nantes')
        make_plant_variety(name='Somebody else\'s')

        response = self.client.get('/plants/variety/', {'plant': self.crop.pk})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['pk'] for row in response.data['results']], [mine.pk])

    def test_a_crop_that_is_not_a_number_is_refused_by_name(self):
        """Rather than answering with the whole catalog."""
        response = self.client.get('/plants/variety/', {'plant': 'Carrot'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('plant', response.data)


class InheritanceCostTests(VarietyInheritanceTestCase):
    """Reporting it costs the page rather than the row."""

    def test_listing_varieties_costs_the_same_however_many_there_are(self):
        """The crop behind every row is loaded with the page.

        The count itself is incidental; what matters is that answering "what
        is this planned by" for each row does not add a query per row.
        """
        for index in range(3):
            make_plant_variety(plant=self.crop, name=f'Variety {index}')
        with self.assertNumQueries(4):
            response = self.client.get('/plants/variety/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 3)

        for index in range(3, 12):
            make_plant_variety(plant=self.crop, name=f'Variety {index}')
        with self.assertNumQueries(4):
            self.client.get('/plants/variety/')
