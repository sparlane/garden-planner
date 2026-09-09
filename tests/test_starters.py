"""The crops a household garden starts with, and what the gardener owns of them."""

from plants.models import Plant, PlantFamily, PlantVariety
from plants.starters import STARTER_SOURCE, ensure_starter_crops
from tests.api import RESTContractTestCase
from tests.factories import make_plant, make_plant_family, make_plant_variety
from workspaces.current import get_current_workspace


class StarterCropTestCase(RESTContractTestCase):
    """One workspace, asked for the crops a garden usually holds."""

    def install(self):
        """Install the starter set the way the screen does."""
        response = self.client.post('/plants/starters/', {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def crop(self, name):
        """Return one installed crop."""
        return Plant.objects.get(name=name)


class StarterInstallTests(StarterCropTestCase):
    """An empty catalog is filled with something worth choosing from."""

    def test_the_set_arrives_filed_under_its_families(self):
        """A gardener recording a sowing should not first invent Apiaceae."""
        self.install()

        carrot = self.crop('Carrot')
        self.assertEqual(carrot.family.name, 'Apiaceae')
        self.assertEqual(carrot.spacing, 50)
        self.assertEqual(carrot.maturity_days_min, 70)

    def test_a_variety_says_only_what_differs_from_its_crop(self):
        """It reads through to the plant for everything else."""
        self.install()

        nantes = PlantVariety.objects.get(name='Nantes')
        self.assertEqual(nantes.plant.name, 'Carrot')
        self.assertIsNone(nantes.spacing)
        self.assertEqual(nantes.maturity_days_min, 65)

    def test_installing_twice_changes_nothing(self):
        """The screen can offer it without anybody having to remember."""
        self.install()
        before = PlantFamily.objects.count(), Plant.objects.count(), PlantVariety.objects.count()

        self.install()

        self.assertEqual(
            (PlantFamily.objects.count(), Plant.objects.count(), PlantVariety.objects.count()),
            before,
        )

    def test_every_installed_record_says_where_it_came_from(self):
        """Which is the difference between a catalog built and one that arrived."""
        self.install()

        self.assertEqual(self.crop('Carrot').reference_source, STARTER_SOURCE)
        self.assertEqual(
            PlantFamily.objects.get(name='Apiaceae').reference_source, STARTER_SOURCE,
        )


class GardenerFactTests(StarterCropTestCase):
    """A figure measured in this garden beats one printed for another."""

    def test_an_untouched_figure_is_still_the_sets(self):
        """Nothing has been said about it but what the set said."""
        self.install()

        self.assertIn('maturity_days_min', self.crop('Carrot').reference_fields())

    def test_an_edited_figure_becomes_the_gardeners(self):
        """No flag is written: the comparison is the flag."""
        self.install()
        carrot = self.crop('Carrot')

        response = self.client.patch(
            f'/plants/plant/{carrot.pk}/', {'maturity_days_min': 95}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('maturity_days_min', response.data['reference_fields'])
        self.assertIn('spacing', response.data['reference_fields'])

    def test_reinstalling_leaves_a_measured_figure_alone(self):
        """This is the whole reason the set remembers what it said."""
        self.install()
        carrot = self.crop('Carrot')
        self.client.patch(
            f'/plants/plant/{carrot.pk}/', {'maturity_days_min': 95}, format='json',
        )

        self.install()

        self.assertEqual(self.crop('Carrot').maturity_days_min, 95)

    def test_reinstalling_refreshes_a_figure_nobody_touched(self):
        """An untouched figure is the set's to correct."""
        self.install()
        carrot = self.crop('Carrot')
        carrot.spacing = None
        carrot.save()

        self.install()

        self.assertEqual(self.crop('Carrot').spacing, 50)


class AdoptionTests(StarterCropTestCase):
    """The set never makes the duplicate the warning would have refused."""

    def test_a_crop_the_gardener_already_typed_is_adopted(self):
        """Compared the way two catalog names are compared everywhere else."""
        family = make_plant_family(name='Apiaceae')
        typed = make_plant(family=family, name='Carrots')

        self.install()

        self.assertEqual(Plant.objects.filter(name__in=('Carrot', 'Carrots')).count(), 1)
        typed.refresh_from_db()
        self.assertEqual(typed.name, 'Carrots')
        self.assertEqual(typed.reference_source, STARTER_SOURCE)
        self.assertEqual(typed.spacing, 50)

    def test_a_figure_the_gardener_had_already_entered_survives_adoption(self):
        """They said it about their own garden before the set turned up."""
        family = make_plant_family(name='Apiaceae')
        typed = make_plant(family=family, name='Carrot', maturity_days_min=95)

        self.install()

        typed.refresh_from_db()
        self.assertEqual(typed.maturity_days_min, 95)
        self.assertNotIn('maturity_days_min', typed.reference_fields())
        self.assertEqual(typed.spacing, 50)


class RetiredStarterTests(StarterCropTestCase):
    """Retiring is something the gardener chose, and the set respects it."""

    def test_a_retired_crop_is_left_where_it_was_put(self):
        """A set that quietly brought it back would be arguing with them."""
        self.install()
        carrot = self.crop('Carrot')
        for variety in carrot.plantvariety_set.all():
            self.client.patch(
                f'/plants/variety/{variety.pk}/', {'active': False}, format='json',
            )
        retired = self.client.patch(
            f'/plants/plant/{carrot.pk}/', {'active': False}, format='json',
        )
        self.assertEqual(retired.status_code, 200, retired.data)

        self.install()

        self.assertFalse(self.crop('Carrot').active)

    def test_nothing_is_installed_under_a_retired_family(self):
        """Which is also what keeps an active crop out from under one."""
        family = make_plant_family(name='Apiaceae')
        family.active = False
        family.save()

        ensure_starter_crops(get_current_workspace())

        self.assertFalse(Plant.objects.filter(name='Carrot').exists())


class StarterScopeTests(StarterCropTestCase):
    """The set fills a catalog without disturbing what is beside it."""

    def test_a_record_the_gardener_made_keeps_no_source(self):
        """It is theirs, and reinstalling does not adopt what it never named."""
        family = make_plant_family(name='Nightshades')
        make_plant_variety(plant=make_plant(family=family, name='Tomatillo'), name='Verde')

        self.install()

        self.assertEqual(
            Plant.objects.get(name='Tomatillo').reference_source, '',
        )
        self.assertEqual(
            PlantVariety.objects.get(name='Verde').reference_source, '',
        )
