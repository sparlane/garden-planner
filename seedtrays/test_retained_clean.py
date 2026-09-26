"""Cleaning a tray that still holds stock kept back before the clean began.

Task 150. A plant retained before the clean is resolved, so the clean has no
outcome left to ask it for, and it is still standing in its cell, so closing
the fill would leave it recorded in a tray that has been emptied and refilled.
The clean names them and refuses; these cover the refusal, what it writes
(nothing), and the two ways out of it.
"""
# pylint: disable=duplicate-code

from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.models import InventoryItem
from inventory.units import UnitCode
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_lifecycle_event,
)
from plantings.repotting import repot_into_pots
from tests.factories import make_inventory_item, make_numbered_container

from .generations import contents_digest, generation_contents, resolved_plants, unresolved_plants
from .models import SeedTrayGeneration, SeedTrayGenerationEvent
from .test_generations import GenerationContentsTestCase


class RetainedStockCleanTests(GenerationContentsTestCase):
    """Task 150: a plant kept back before the clean has to leave the tray first."""

    def retain(self, plant):
        """Keep one seedling back, which resolves it without moving it."""
        return record_lifecycle_event(plant, self.user, OutcomeRequest(
            event_type=EventType.RETAINED,
            reason='Mother stock.',
        ))

    def test_contents_separate_what_is_owed_from_what_must_be_moved(self):
        """The screen asks about one list and refuses over the other."""
        sowing = self.sow()
        asked = self.germinate(sowing)
        kept = self.germinate(sowing, cell_index=0)
        self.retain(kept)

        contents = generation_contents(self.generation)

        self.assertEqual([row.pk for row in contents['plants']], [asked.pk])
        self.assertEqual([row.pk for row in contents['resolved']], [kept.pk])
        # The two named readings are wrappers over the one pass the contents
        # take, so they have to answer what the contents carry.
        self.assertEqual(
            [row.pk for row in unresolved_plants(self.generation)],
            [asked.pk],
        )
        self.assertEqual(
            [row.pk for row in resolved_plants(self.generation)],
            [kept.pk],
        )

    def test_a_plant_retained_before_the_clean_refuses_it(self):
        """Nothing can be recorded against it, so its cell cannot be emptied."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        self.retain(plant)

        with self.assertRaises(ValidationError) as caught:
            self.close()

        message = ' '.join(caught.exception.messages)
        self.assertIn(str(plant.pk), message)
        self.assertIn('move them out', message)

    def test_the_refusal_names_every_plant_still_standing(self):
        """One trip to the bench, not one refusal per seedling."""
        sowing = self.sow(quantity=4, allocations=((0, 2), (1, 2)))
        first = self.germinate(sowing)
        second = self.germinate(sowing, cell_index=1)
        self.retain(first)
        self.retain(second)

        with self.assertRaises(ValidationError) as caught:
            self.close()

        message = ' '.join(caught.exception.messages)
        self.assertIn(str(first.pk), message)
        self.assertIn(str(second.pk), message)

    def test_the_refusal_writes_nothing(self):
        """A refused clean leaves the fill open and the plant where it was."""
        self.apply_media()
        sowing = self.sow(quantity=4, allocations=((0, 2),))
        plant = self.germinate(sowing)
        self.retain(plant)
        events = plant.lifecycle_events.count()

        with self.assertRaises(ValidationError):
            self.close()

        self.generation.refresh_from_db()
        self.assertEqual(self.generation.status, SeedTrayGeneration.Status.OPEN)
        self.assertEqual(self.generation.residuals.count(), 0)
        self.assertFalse(self.generation.events.filter(
            event_type=SeedTrayGenerationEvent.EventType.CLOSED,
        ).exists())
        self.assertEqual(plant.lifecycle_events.count(), events)
        self.assertTrue(plant.locations.filter(ended__isnull=True).exists())

    def test_repotting_the_kept_plant_lets_the_clean_run(self):
        """The repot run is the way out the tray screen offers."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        self.retain(plant)
        pot = make_numbered_container(
            item=make_inventory_item(
                category=InventoryItem.Category.POT_CONTAINER,
                base_unit=UnitCode.EACH,
                tracking_mode=InventoryItem.TrackingMode.MIXED,
            ),
            location=self.location,
        )

        repot_into_pots(self.workspace, self.user, [(plant.pk, pot.pk)])
        generation, _ = self.close()

        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.RETAINED)
        self.assertEqual(
            plant.locations.get(ended__isnull=True).container_unit_id,
            pot.pk,
        )

    def test_moving_the_kept_plant_out_lets_the_clean_run(self):
        """Any ordinary move answers the question the clean cannot."""
        sowing = self.sow()
        plant = self.germinate(sowing)
        self.retain(plant)
        plant.locations.update(ended=timezone.now())

        generation, _ = self.close()

        self.assertEqual(generation.status, SeedTrayGeneration.Status.CLOSED)
        self.assertEqual(plant_lifecycle_summary(plant).state, LifecycleState.RETAINED)

    def test_moving_the_kept_plant_out_makes_an_earlier_confirmation_stale(self):
        """A blocked plant is in the digest, so its leaving is a change too.

        The other direction needs nothing new: retaining a plant the form asked
        about drops it from `plants` and moves the digest by itself. This is the
        direction only the `resolved` rows can see — the plants the clean asks
        about are the same before and after, while the screen the operator is
        looking at still says the tray cannot be cleaned.
        """
        sowing = self.sow()
        plant = self.germinate(sowing)
        self.retain(plant)
        digest = contents_digest(generation_contents(self.generation))
        plant.locations.update(ended=timezone.now())

        with self.assertRaises(ValidationError) as caught:
            self.close(digest=digest)

        self.assertIn('changed after', ' '.join(caught.exception.messages))
