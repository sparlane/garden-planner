"""Carrying a crop catalog out of one garden and into another."""

from plants.models import MaturityBasis, Plant, PlantFamily, PlantVariety
from plants.reference_sets import CROP_FIELDS, ReferenceSetSerializer, export_reference_set
from plants.starters import CROP_FIGURES, starter_document
from tests.api import RESTContractTestCase
from tests.factories import make_plant, make_plant_family, make_plant_variety
from workspaces.current import get_current_workspace

#: A small document, written the way one arriving from a garden club would be.
CLUB_SET = {
    'source': 'Valley Garden Club',
    'families': [
        {
            'name': 'Apiaceae',
            'notes': 'Umbellifers.',
            'plants': [
                {
                    'name': 'Carrot',
                    'spacing': 50,
                    'maturity_days_min': 70,
                    'maturity_days_max': 90,
                    'maturity_basis': MaturityBasis.SEED,
                    'varieties': [
                        {'name': 'Nantes', 'maturity_days_min': 65},
                    ],
                },
            ],
        },
    ],
}


class ReferenceSetTestCase(RESTContractTestCase):
    """One workspace, carrying its catalog in and out."""

    url = '/plants/reference-set/'

    def export(self):
        """Read this workspace's catalog out as a document."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def install(self, document, expected=201):
        """Install a document the way the screen does."""
        response = self.client.post(self.url, document, format='json')
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    def empty_the_catalog(self):
        """Clear every crop record, leaving a garden with nothing filed."""
        PlantVariety.objects.all().delete()
        Plant.objects.all().delete()
        PlantFamily.objects.all().delete()


class ExportTests(ReferenceSetTestCase):
    """What a garden writes out is what that garden now says."""

    def test_the_catalog_leaves_as_a_filed_tree(self):
        """Which is the shape it is read in, so nothing has to be rebuilt."""
        family = make_plant_family(name='Apiaceae', notes='Umbellifers.')
        plant = make_plant(family=family, name='Carrot', spacing=50, maturity_days_min=70)
        make_plant_variety(plant=plant, name='Nantes', maturity_days_min=65)

        document = self.export()

        [exported] = [entry for entry in document['families'] if entry['name'] == 'Apiaceae']
        self.assertEqual(exported['notes'], 'Umbellifers.')
        [crop] = exported['plants']
        self.assertEqual((crop['name'], crop['spacing'], crop['maturity_days_min']), ('Carrot', 50, 70))
        self.assertEqual(crop['varieties'][0]['name'], 'Nantes')

    def test_a_variety_carries_only_what_it_overrides(self):
        """It reads through to its crop for the rest, in either garden."""
        plant = make_plant(name='Carrot', spacing=50)
        make_plant_variety(plant=plant, name='Nantes', maturity_days_min=65, notes=None)

        document = self.export()

        [crop] = [entry for entry in document['families'][0]['plants'] if entry['name'] == 'Carrot']
        self.assertEqual(crop['varieties'][0], {'name': 'Nantes', 'maturity_days_min': 65})

    def test_a_retired_record_stays_where_the_gardener_put_it(self):
        """A document carrying it would install a discarded typo next door."""
        family = make_plant_family(name='Apiaceae')
        retired = make_plant(family=family, name='Carrott')
        retired.active = False
        retired.save()

        document = self.export()

        [exported] = [entry for entry in document['families'] if entry['name'] == 'Apiaceae']
        self.assertEqual(exported['plants'], [])

    def test_a_larger_catalog_costs_no_more_queries(self):
        """One query a level: the tree is walked through what was fetched."""
        for family_index in range(3):
            family = make_plant_family(name=f'Family {family_index}')
            for crop_index in range(3):
                plant = make_plant(family=family, name=f'Crop {family_index}-{crop_index}')
                make_plant_variety(plant=plant, name=f'Variety {family_index}-{crop_index}')

        workspace = get_current_workspace()

        with self.assertNumQueries(3):
            export_reference_set(workspace)

    def test_the_document_is_named_after_the_garden_it_came_from(self):
        """Which is what the receiving catalog shows against every record."""
        make_plant_family(name='Apiaceae')

        self.assertEqual(self.export()['source'], get_current_workspace().name)

    def test_what_a_garden_writes_out_is_what_an_import_accepts(self):
        """One shape, defined once and read in both directions."""
        plant = make_plant(name='Carrot', spacing=50)
        make_plant_variety(plant=plant, name='Nantes', maturity_days_min=65)

        document = ReferenceSetSerializer(data=self.export())

        self.assertTrue(document.is_valid(), document.errors)

    def test_the_provenance_of_a_figure_does_not_travel(self):
        """It says which numbers this gardener owns, in this garden."""
        make_plant(name='Carrot', spacing=50)

        crop = self.export()['families'][0]['plants'][0]

        self.assertNotIn('reference_source', crop)
        self.assertNotIn('reference_fields', crop)


class RoundTripTests(ReferenceSetTestCase):
    """A catalog exported from one garden fills the next one along."""

    def test_an_exported_catalog_rebuilds_the_one_it_came_from(self):
        """Every figure, filed under the family it was filed under."""
        family = make_plant_family(name='Apiaceae')
        plant = make_plant(family=family, name='Carrot', spacing=50, maturity_days_max=90)
        make_plant_variety(plant=plant, name='Nantes', maturity_days_min=65)
        document = self.export()

        self.empty_the_catalog()
        self.install(document)

        carrot = Plant.objects.get(name='Carrot')
        self.assertEqual((carrot.family.name, carrot.spacing, carrot.maturity_days_max), ('Apiaceae', 50, 90))
        self.assertEqual(carrot.plantvariety_set.get().maturity_days_min, 65)

    def test_everything_that_arrives_is_reference_data(self):
        """However hard won: it was measured in somebody else's soil."""
        self.install(CLUB_SET)

        carrot = Plant.objects.get(name='Carrot')
        self.assertEqual(carrot.reference_source, 'Valley Garden Club')
        self.assertIn('spacing', carrot.reference_fields())
        self.assertEqual(
            PlantVariety.objects.get(name='Nantes').reference_source, 'Valley Garden Club',
        )

    def test_a_figure_this_gardener_measured_survives_a_second_import(self):
        """The whole reason the record remembers what the set said."""
        self.install(CLUB_SET)
        carrot = Plant.objects.get(name='Carrot')
        self.client.patch(f'/plants/plant/{carrot.pk}/', {'maturity_days_min': 95}, format='json')

        self.install(CLUB_SET)

        carrot.refresh_from_db()
        self.assertEqual(carrot.maturity_days_min, 95)
        self.assertNotIn('maturity_days_min', carrot.reference_fields())

    def test_a_crop_the_gardener_already_typed_is_adopted(self):
        """The same rule the duplicate warning applies to a typed name."""
        typed = make_plant(family=make_plant_family(name='Apiaceae'), name='Carrots')

        self.install(CLUB_SET)

        self.assertEqual(Plant.objects.filter(name__in=('Carrot', 'Carrots')).count(), 1)
        typed.refresh_from_db()
        self.assertEqual((typed.name, typed.spacing), ('Carrots', 50))

    def test_nothing_is_installed_under_a_retired_family(self):
        """Which is what keeps an active crop out from under one."""
        family = make_plant_family(name='Apiaceae')
        family.active = False
        family.save()

        self.install(CLUB_SET)

        self.assertFalse(Plant.objects.filter(name='Carrot').exists())

    def test_installing_twice_creates_nothing_the_second_time(self):
        """So a screen may offer it without anybody having to remember."""
        self.install(CLUB_SET)
        before = PlantFamily.objects.count(), Plant.objects.count(), PlantVariety.objects.count()

        self.install(CLUB_SET)

        self.assertEqual(
            (PlantFamily.objects.count(), Plant.objects.count(), PlantVariety.objects.count()),
            before,
        )

    def test_the_installed_catalog_is_reported_back(self):
        """A screen shows what the catalog holds, not only what changed."""
        installed = self.install(CLUB_SET)

        self.assertEqual([entry['name'] for entry in installed['families']], ['Apiaceae'])
        self.assertEqual([entry['name'] for entry in installed['plants']], ['Carrot'])
        self.assertEqual([entry['name'] for entry in installed['varieties']], ['Nantes'])


class RefusalTests(ReferenceSetTestCase):
    """A document arrives whole or not at all."""

    def test_a_document_naming_no_set_is_refused(self):
        """Its records would be indistinguishable from ones somebody typed."""
        self.install({'families': CLUB_SET['families']}, expected=400)

        self.assertFalse(Plant.objects.exists())

    def test_one_bad_crop_installs_none_of_the_document(self):
        """Reading a file to find out how much of it arrived is worse."""
        document = {
            'source': 'Valley Garden Club',
            'families': [
                CLUB_SET['families'][0],
                {'name': 'Fabaceae', 'plants': [{'name': 'Pea', 'maturity_basis': 'compost'}]},
            ],
        }

        self.install(document, expected=400)

        self.assertFalse(PlantFamily.objects.exists())

    def test_the_route_rejects_an_anonymous_reader(self):
        """A catalog is workspace data like any other."""
        self.assert_authentication_required([self.url])


class StarterDocumentTests(ReferenceSetTestCase):
    """The set that ships in the box is one document among several."""

    def test_the_starter_set_is_a_document_an_import_would_accept(self):
        """Which is why installing either one is the same code path."""
        document = ReferenceSetSerializer(data=starter_document())

        self.assertTrue(document.is_valid(), document.errors)

    def test_the_starter_columns_stay_in_step_with_the_document(self):
        """They name the columns of a table a reordered list would misread."""
        self.assertEqual(CROP_FIGURES, tuple(field for field in CROP_FIELDS if field != 'notes'))

    def test_a_starter_catalog_exports_as_it_arrived(self):
        """What a set installed is what the next garden is offered."""
        self.client.post('/plants/starters/', {}, format='json')

        exported = export_reference_set(get_current_workspace())

        [apiaceae] = [entry for entry in exported['families'] if entry['name'] == 'Apiaceae']
        [carrot] = [entry for entry in apiaceae['plants'] if entry['name'] == 'Carrot']
        self.assertEqual(carrot['spacing'], 50)
        self.assertEqual(carrot['varieties'][0], {'name': 'Nantes', 'maturity_days_min': 65, 'maturity_days_max': 75})
