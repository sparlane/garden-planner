"""Concurrency proofs for the locks cleaning a tray relies on.

`close_generation` takes the generation lock, then plants, then batches, then
lots. `applications.services.post_application` documents why the last three run
in that order; the generation is a new outermost level that nothing else takes,
so extending the chain rather than starting a new one is what keeps this
compatible with sowing, harvesting, and posting an application.

Real row locks are what these prove, so they need a database that honours
`SELECT ... FOR UPDATE`.
"""
# pylint: disable=duplicate-code

from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.models import PlantLifecycleEvent, SpecificPlant
from tests.factories import (
    make_seed_tray,
    make_specific_plant,
    make_specific_plant_location,
)
from workspaces.models import Workspace

from .generations import (
    CloseRequest,
    PlantDisposition,
    close_generation,
    open_generation,
)
from .models import SeedTrayGeneration


class GenerationConcurrencyTestCase(TransactionTestCase):
    """Shared fixture teardown for the tray-cleaning race tests."""

    #: Set by each subclass's setUp before any thread starts.
    generation_pk = None
    user_pk = None

    def _post_teardown(self):
        """Restore migration seed data removed by transactional test flushing."""
        super()._post_teardown()
        if not Workspace.objects.filter(pk=settings.CURRENT_WORKSPACE_ID).exists():
            Workspace.objects.create(
                pk=settings.CURRENT_WORKSPACE_ID,
                name='My Garden',
            )

    def _clean(self, dispositions=()):
        """Attempt one clean from an independent connection."""
        close_old_connections()
        generation = SeedTrayGeneration.objects.get(pk=self.generation_pk)
        user = get_user_model().objects.get(pk=self.user_pk)
        try:
            close_generation(generation, user, CloseRequest(
                reason='End of the run.',
                plants=dispositions,
            ))
        except ValidationError:
            result = 'rejected'
        else:
            result = 'cleaned'
        close_old_connections()
        return result


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentCleanTests(GenerationConcurrencyTestCase):
    """Two operators cleaning the same tray resolve its contents once."""

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(username='clean-racer')
        self.user_pk = user.pk
        tray = make_seed_tray()
        self.generation_pk = open_generation(tray, user).pk

    def test_only_one_clean_closes_the_generation(self):
        """The loser is refused outright rather than half-applied."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [pool.submit(self._clean), pool.submit(self._clean)]
            )

        self.assertEqual(results, ['cleaned', 'rejected'])
        generation = SeedTrayGeneration.objects.get(pk=self.generation_pk)
        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)
        self.assertEqual(
            generation.events.filter(event_type='closed').count(),
            1,
        )


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentCleanAndOutcomeTests(GenerationConcurrencyTestCase):
    """A clean and a plant-level outcome cannot both resolve one seedling.

    The clean writes plant facts, and recording an outcome directly writes the
    same kind of fact without touching the generation at all. The plant lock is
    therefore the only thing keeping one seedling from ending twice, which is
    the same guarantee `plantings.test_harvest_concurrency` relies on.
    """

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(username='outcome-racer')
        self.user_pk = user.pk
        plant = make_specific_plant()
        record_germination_event(plant, user)
        make_specific_plant_location(specific_plant=plant)
        self.plant_pk = plant.pk
        tray = plant.cell_planting.cell.tray
        generation = open_generation(tray, user, opened_at=timezone.now())
        sowing = plant.cell_planting.seed_tray_planting
        type(sowing).objects.filter(pk=sowing.pk).update(generation=generation)
        self.generation_pk = generation.pk

    def _cull_plant(self):
        """Attempt to record the plant as culled from another connection."""
        close_old_connections()
        plant = SpecificPlant.objects.get(pk=self.plant_pk)
        user = get_user_model().objects.get(pk=self.user_pk)
        try:
            record_lifecycle_event(plant, user, OutcomeRequest(EventType.CULLED))
        except ValidationError:
            result = 'rejected'
        else:
            result = 'culled'
        close_old_connections()
        return result

    def test_a_plant_is_resolved_by_exactly_one_writer(self):
        """Whichever wins, the seedling ends with a single final outcome."""
        dispositions = (PlantDisposition(self.plant_pk, 'failed', 'Damped off.'),)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result()
                for future in [
                    pool.submit(self._clean, dispositions),
                    pool.submit(self._cull_plant),
                ]
            )

        outcomes = PlantLifecycleEvent.objects.filter(
            plant_id=self.plant_pk,
            event_type__in=(
                PlantLifecycleEvent.EventType.FAILED,
                PlantLifecycleEvent.EventType.CULLED,
            ),
        )
        self.assertEqual(outcomes.count(), 1)
        self.assertIn('rejected', results)
