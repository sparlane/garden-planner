"""Where a released plant goes, and what carries it there.

Task 130: closing a quarantine case has to say where the stock went, because
`released_available` leaves a plant exactly where the quarantine put it and the
closed case stops refusing sales. These cover the rule itself and then the
three ways a plant can be standing somewhere — on its own feet, in a tray or
pot that carries it, and in a counted pot fill that cannot be carried at all.
"""

# Test names describe behavior directly.
# pylint: disable=missing-function-docstring,duplicate-code

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError

from inventory.ledger import IndividualizationRequest, individualize_lot_units
from plantings.counted_fills import plant_counted_fill
from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from plantings.models import PlantCohort, SpecificPlantLocation
from plantings.movement import move_specific_plant
from plantings.register import RegisterFilters, register_queryset
from seedtrays.container_fills import open_counted_fill, open_numbered_fill
from seedtrays.models import SeedTrayGeneration
from tests.factories import (
    make_inventory_item,
    make_location,
    make_specific_plant,
    make_specific_plant_location,
    make_stock_lot,
)

from .availability import case_is_active, is_quarantined
from .models import HealthObservation, QuarantineAction
from .operations import act_on_quarantine, quarantine_observation
from .services import preview_observation, record_observation
from .test_operations import HealthOperationTestCase


class ReleaseDestinationTestCase(HealthOperationTestCase):
    """A case whose stock stands somewhere, in each way stock can stand.

    Task 130's rules are about where a member is and what carries it, so the
    fixtures build a plant on a bench of its own, a plant in a numbered pot,
    and a plant in a counted pot fill, and open cases over them.
    """

    def act(self, case, reason='Inspection found nothing.', **values):
        return act_on_quarantine(
            self.workspace, None, case,
            action_name=QuarantineAction.Action.RELEASE,
            idempotency_key=uuid4(), reason=reason, **values,
        )

    def quarantine_bench(self):
        return make_location(
            workspace=self.workspace, location_type='quarantine',
        )

    def offered_plant(self, bench=None):
        """One plant on offer, standing on a bench of its own."""
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        make_specific_plant_location(
            specific_plant=plant,
            location_type=SpecificPlantLocation.LOCATION,
            seed_tray_cell=None,
            location=bench or make_location(workspace=self.workspace),
        )
        return plant

    def case_on_the_quarantine_bench(self, target_type, target):
        """Open one case that moves its stock onto a quarantine bench."""
        case, _action = quarantine_observation(
            self.workspace, None, self.observe(target_type, target),
            idempotency_key=uuid4(), reason='Keep it away from healthy stock.',
            destination=self.quarantine_bench(),
        )
        return case

    def standing_at(self, plant):
        return SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        ).location

    def pot_lot(self, bench, quantity='4'):
        """Empty pots standing where a fill can claim them."""
        return make_stock_lot(
            item=make_inventory_item(
                category='pot_container', base_unit='each', tracking_mode='mixed',
            ),
            location=bench, quantity=quantity, base_unit_cost=Decimal('3'),
        )

    def potted_plant(self, bench):
        """One plant in a counted pot fill, as a quarantined return leaves it."""
        fill = open_counted_fill(
            self.workspace, None, self.pot_lot(bench), bench, 1, returned=True,
        )
        plant = make_specific_plant(workspace=self.workspace)
        record_lifecycle_event(
            plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
        )
        plant_counted_fill(self.workspace, None, fill, [plant.pk])
        return plant, fill

    def numbered_pot_plants(self, bench, count=1):
        """Plants sharing one numbered pot, which carries its own placement.

        Several plants in one pot is ordinary, the way a multigerm cell holds
        several seedlings, and it is what makes the pot rather than the plant
        the thing that travels.
        """
        lot = self.pot_lot(bench, quantity='1')
        unit, = individualize_lot_units(
            self.workspace, None, IndividualizationRequest(lot, bench, 1),
        )
        open_numbered_fill(self.workspace, None, unit, returned=True)
        plants = []
        for _ in range(count):
            plant = make_specific_plant(workspace=self.workspace)
            record_lifecycle_event(
                plant, None, OutcomeRequest(EventType.READY, reason='Ready for sale.'),
            )
            move_specific_plant(plant, {
                'location_type': SpecificPlantLocation.CONTAINER_UNIT,
                'container_unit': unit,
            }, user=None)
            plants.append(plant)
        return plants, unit

    def numbered_pot_plant(self, bench):
        """One plant in a numbered pot."""
        plants, unit = self.numbered_pot_plants(bench)
        return plants[0], unit

    def case_over(self, *plants):
        """Open one overlay case over exactly these plants, moving nothing."""
        scopes = [{'type': 'plant', 'id': plant.pk} for plant in plants]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Inspected on the bench.',
        )
        return self.quarantine(observation)


class ReleaseDestinationTests(ReleaseDestinationTestCase):
    """Task 130: closing a case says where the stock it held went.

    `released_available` leaves a plant where the quarantine put it and the
    closed case stops refusing sales, so a release that names no destination
    leaves saleable stock standing on the quarantine bench. The destination is
    owed exactly when there is a bench to come off.
    """

    def test_a_release_from_a_quarantine_bench_needs_a_destination(self):
        """Verification 1: the case may not close over stranded stock."""
        plant = self.offered_plant()
        case = self.case_on_the_quarantine_bench('plant', plant)
        placed_at = self.standing_at(plant)

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Plant {plant.pk}.'):
            self.act(case)

        self.assertTrue(case_is_active(case))
        self.assertFalse(
            case.actions.filter(action=QuarantineAction.Action.RELEASE).exists(),
        )
        self.assertEqual(self.standing_at(plant), placed_at)
        self.assertTrue(is_quarantined(plant))

    def test_a_released_plant_stands_where_the_release_sends_it(self):
        """Verification 3: the move ends the quarantine placement, not the release."""
        plant = self.offered_plant()
        case = self.case_on_the_quarantine_bench('plant', plant)
        quarantine_placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        bench = make_location(workspace=self.workspace, name='Sales bench')

        action = self.act(case, destination=bench)

        quarantine_placement.refresh_from_db()
        self.assertEqual(quarantine_placement.ended, action.occurred_at)
        self.assertEqual(self.standing_at(plant), bench)
        row = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertEqual(row.standing_at, bench.pk)
        self.assertTrue(row.sellable)
        self.assertFalse(row.quarantined)

    def test_a_release_that_moves_no_stock_needs_no_destination(self):
        """An overlay case left the plant where it was, so nothing is owed."""
        bench = make_location(workspace=self.workspace, name='Growing-on bench')
        plant = self.offered_plant(bench=bench)
        case = self.quarantine(self.observe('plant', plant))

        self.act(case)

        self.assertFalse(case_is_active(case))
        self.assertEqual(self.standing_at(plant), bench)
        row = register_queryset(self.workspace, RegisterFilters()).get(pk=plant.pk)
        self.assertTrue(row.sellable)

    def test_a_cohort_block_on_a_quarantine_bench_needs_a_destination(self):
        """A block is counted stock in a place, so it is asked the same thing."""
        plant = make_specific_plant(workspace=self.workspace)
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=4,
        )
        case = self.case_on_the_quarantine_bench('cohort', cohort)

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Cohort {cohort.pk}.'):
            self.act(case)

        self.assertTrue(case_is_active(case))
        bench = make_location(workspace=self.workspace, name='Block bench')
        self.act(case, destination=bench)
        cohort.refresh_from_db()
        self.assertFalse(case_is_active(case))
        self.assertEqual(cohort.location_id, bench.pk)
        self.assertEqual(cohort.quantity, 4)

    def test_a_release_cannot_send_stock_to_a_ledger_location(self):
        """An adjustment location is posted against, not stood on."""
        plant = self.offered_plant()
        case = self.case_on_the_quarantine_bench('plant', plant)
        ledger = make_location(
            workspace=self.workspace, location_type='adjustment',
            name='Stock adjustment', code='ADJUST',
        )

        with self.assertRaisesMessage(
                ValidationError,
                'Stock cannot stand in an adjustment or seed-packet location.'):
            self.act(case, destination=ledger)

        self.assertTrue(case_is_active(case))

    def test_a_release_names_every_member_left_in_quarantine(self):
        """One refusal lists the whole reviewed set, as the member checks do."""
        plant = self.offered_plant()
        cohort = PlantCohort.objects.create(
            workspace=self.workspace, batch=plant.batch, quantity=4,
        )
        scopes = [
            {'type': 'plant', 'id': plant.pk},
            {'type': 'cohort', 'id': cohort.pk},
        ]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, None, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=self.observation_type,
            severity=HealthObservation.Severity.HIGH,
            notes='Both the bench and the block inspected.',
        )
        case, _action = quarantine_observation(
            self.workspace, None, observation,
            idempotency_key=uuid4(), reason='Keep it away from healthy stock.',
            destination=self.quarantine_bench(),
        )

        with self.assertRaisesMessage(
                ValidationError,
                f'Plant {plant.pk}, Cohort {cohort.pk}. '
                'Name the location it is going to.'):
            self.act(case)

        self.assertTrue(case_is_active(case))


class ReleaseCarrierTests(ReleaseDestinationTestCase):
    """What travels when a released plant is not standing on its own feet.

    A tray and a numbered pot hold their own placement, so the carrier moves
    and every plant in it goes along. A counted pot fill cannot move at all,
    and the release says so rather than quietly taking the plant out of it.
    """

    def test_a_release_will_not_take_a_plant_out_of_its_pot(self):
        """A counted fill cannot be carried, so the release says so instead.

        Moving the plant would make its placement a plain bench one, leave the
        fill open at the quarantine location with nobody in it, and freeze its
        shares on a departure nobody recorded.
        """
        bench = self.quarantine_bench()
        plant, fill = self.potted_plant(bench)
        case = self.quarantine(self.observe('plant', plant))

        with self.assertRaisesMessage(
                ValidationError,
                f'A counted pot fill cannot leave the location it was opened at: '
                f'Plant {plant.pk} in pot fill {fill.pk}.'):
            self.act(case)
        with self.assertRaisesMessage(
                ValidationError, 'Take the plant out of its pot'):
            self.act(case, destination=make_location(workspace=self.workspace))

        self.assertTrue(case_is_active(case))
        placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertEqual(placement.container_fill_id, fill.pk)
        self.assertEqual(placement.location_id, bench.pk)
        fill.refresh_from_db()
        self.assertEqual(fill.status, SeedTrayGeneration.Status.OPEN)

    def test_a_plant_taken_out_of_its_pot_then_releases_on_its_own(self):
        """The refusal points somewhere real: the departure is the operator's."""
        plant, _fill = self.potted_plant(self.quarantine_bench())
        case = self.quarantine(self.observe('plant', plant))
        bench = make_location(workspace=self.workspace, name='Sales bench')
        move_specific_plant(plant, {
            'location_type': SpecificPlantLocation.LOCATION, 'location': bench,
        }, user=None)

        self.act(case)

        self.assertFalse(case_is_active(case))
        self.assertEqual(self.standing_at(plant), bench)

    def test_a_numbered_pot_leaves_quarantine_carrying_its_plant(self):
        """A pot holds its own placement, so the pot is what moves."""
        plant, unit = self.numbered_pot_plant(self.quarantine_bench())
        case = self.quarantine(self.observe('plant', plant))

        with self.assertRaisesMessage(
                ValidationError,
                f'Released stock would stay in quarantine: Plant {plant.pk}.'):
            self.act(case)

        bench = make_location(workspace=self.workspace, name='Sales bench')
        action = self.act(case, destination=bench)

        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, bench.pk)
        placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertEqual(placement.location_type, SpecificPlantLocation.CONTAINER_UNIT)
        self.assertEqual(placement.container_unit_id, unit.pk)
        self.assertIsNotNone(action.results.get().stock_movement)

    def test_one_pot_of_reviewed_plants_makes_one_movement(self):
        """The pot is one thing, so it travels once and keeps its passengers."""
        plants, unit = self.numbered_pot_plants(self.quarantine_bench(), count=2)
        case = self.case_over(*plants)
        bench = make_location(workspace=self.workspace, name='Sales bench')

        action = self.act(case, destination=bench)

        unit.refresh_from_db()
        self.assertEqual(unit.current_location_id, bench.pk)
        movements = {
            result.stock_movement_id for result in action.results.all()
            if result.stock_movement_id
        }
        self.assertEqual(len(movements), 1)
        for plant in plants:
            placement = SpecificPlantLocation.objects.get(
                specific_plant=plant, ended__isnull=True,
            )
            self.assertEqual(placement.container_unit_id, unit.pk)
            self.assertEqual(
                placement.location_type, SpecificPlantLocation.CONTAINER_UNIT,
            )

    def test_a_pot_carrying_unreviewed_plants_is_not_moved(self):
        """A pot cannot travel without the plants nobody looked at."""
        plants, unit = self.numbered_pot_plants(self.quarantine_bench(), count=2)
        reviewed = plants[0]
        unreviewed = plants[1]
        case = self.case_over(reviewed)
        bench = make_location(workspace=self.workspace, name='Sales bench')

        with self.assertRaisesMessage(
                ValidationError, f'Pot {unit.pk} also carries unreviewed plants.'):
            self.act(case, destination=bench)

        unit.refresh_from_db()
        self.assertNotEqual(unit.current_location_id, bench.pk)
        self.assertTrue(case_is_active(case))
        self.assertTrue(is_quarantined(reviewed))
        self.assertFalse(is_quarantined(unreviewed))

    def test_opening_a_case_will_not_take_a_plant_out_of_its_pot(self):
        """The same hole was on the opening side, where the move is the point."""
        bench = make_location(workspace=self.workspace, name='Propagation bench')
        plant, fill = self.potted_plant(bench)

        with self.assertRaisesMessage(
                ValidationError,
                f'A counted pot fill cannot leave the location it was opened at: '
                f'Plant {plant.pk} in pot fill {fill.pk}.'):
            quarantine_observation(
                self.workspace, None, self.observe('plant', plant),
                idempotency_key=uuid4(), reason='Keep it away from healthy stock.',
                destination=self.quarantine_bench(),
            )

        self.assertFalse(is_quarantined(plant))
        placement = SpecificPlantLocation.objects.get(
            specific_plant=plant, ended__isnull=True,
        )
        self.assertEqual(placement.container_fill_id, fill.pk)
        self.assertEqual(placement.location_id, bench.pk)

    def test_a_potted_member_anywhere_blocks_the_destination_first(self):
        """The destination is the whole case's, so any pot in it is the blocker.

        One member stranded in quarantine and one standing perfectly well in a
        counted pot: naming a bench would be refused for the pot, so the pot is
        what the operator is told about.
        """
        stranded = self.offered_plant(bench=self.quarantine_bench())
        potted, fill = self.potted_plant(
            make_location(workspace=self.workspace, name='Propagation bench'),
        )
        case = self.case_over(stranded, potted)

        with self.assertRaisesMessage(
                ValidationError,
                f'A counted pot fill cannot leave the location it was opened at: '
                f'Plant {potted.pk} in pot fill {fill.pk}.'):
            self.act(case)

        self.assertTrue(case_is_active(case))
