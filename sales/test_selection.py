"""What the allocation preview promises, allocation itself has to honour."""

import ast
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from inventory.models import InventoryUnit
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.cohorts import change_cohort, observe_cohort
from plantings.models import CohortOperation, PlantCohort, SpecificPlant
from tests.api import RESTContractTestCase
from tests.factories import (
    make_production_batch,
    make_seed_tray,
    make_seed_tray_cell_planting,
    make_specific_plant,
    quarantine_stock,
)
from workspaces.models import Workspace, get_current_workspace

from . import services
from .models import SalesOrder


#: Why a seedling line can refuse a plant, in the order `_target_error` and
#: `preview_targets` decide them. Shared so the reachability walk below and the
#: table it is checked against cannot drift apart.
SEEDLING_REASONS = (
    'wrong_variety',
    'not_sellable',
    'quarantined',
    'already_reserved',
    'wrong_workspace',
    'unknown',
)

#: Why a tray line can refuse a serialized unit. A tray line reaches the
#: workspace and unknown-identity checks the same way a seedling line does, so
#: only the two it decides for itself are listed.
TRAY_REASONS = ('wrong_item', 'not_available')

#: Why a counted line can refuse a draw on a lot. It shares `wrong_item` with
#: a tray line and `unknown` with both, and adds the two only a pool can have:
#: a place that is not one of ours, and not enough loose stock standing there.
LOT_REASONS = ('unknown_location', 'insufficient_stock')

#: Why a cohort line can refuse a draw on a block. It shares `wrong_variety`,
#: `not_sellable`, `quarantined`, `wrong_workspace` and `unknown` with a
#: seedling line and `insufficient_stock` with a lot draw, and adds the one
#: only an anonymous block has: it has changed since the count was chosen
#: against the figure on screen.
COHORT_REASONS = ('stale_revision',)

#: The refusals a cohort draw borrows from the other two kinds of selection.
#: Listed so the reachability walk covers every reason a cohort draw can carry
#: without claiming any of them as its own.
SHARED_COHORT_REASONS = (
    'wrong_variety', 'not_sellable', 'quarantined', 'wrong_workspace',
    'insufficient_stock', 'unknown',
)


def declared_conflict_reasons():
    """Return every reason the selection code can attach to a target.

    The reasons are bare strings rather than a choices class, so there is
    nothing to enumerate at runtime. They are read out of the module instead,
    which keeps the table below answerable to the code rather than to whoever
    last remembered to update it. Every refusal lands in a function whose name
    ends in `_error` or in a conflict dict, so both are swept rather than a
    named handful that a split would quietly leave behind.
    """
    tree = ast.parse(Path(services.__file__).read_text(encoding='utf-8'))
    reasons = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.endswith('_error'):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    reasons.update(_strings_in(inner.value))
        if isinstance(node, ast.Dict):
            reasons.update(_reason_values(node))
    return reasons


def _is_string(node):
    """Return whether one syntax node is a string literal."""
    if not isinstance(node, ast.Constant):
        return False
    return isinstance(node.value, str)


def _strings_in(node):
    """Return every string literal one expression can evaluate to."""
    return {inner.value for inner in ast.walk(node) if _is_string(inner)}


def _reason_values(node):
    """Return the literal values a dict display assigns to its reason key."""
    values = set()
    for key, value in zip(node.keys, node.values):
        if not _is_string(key) or key.value != 'reason':
            continue
        if _is_string(value):
            values.add(value.value)
    return values


class SelectionFixture(RESTContractTestCase):
    """One nursery workspace with seedling and tray order lines."""

    orders_url = '/sales/orders/'
    lines_url = '/sales/order-lines/'

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.save()
        # Every plant a line can be filled from has to share its variety, and
        # a plant takes its batch from the cell it germinated in.
        self.origin = make_seed_tray_cell_planting()

    def plant(self):
        """Create one ungerminated plant of the fixture's single variety."""
        return make_specific_plant(
            workspace=self.workspace,
            cell_planting=self.origin,
        )

    def ready_plant(self):
        """Create a germinated plant recorded as ready for sale."""
        plant = self.plant()
        record_germination_event(plant, self.user)
        record_lifecycle_event(plant, self.user, OutcomeRequest(EventType.READY))
        return plant

    def quarantine(self, plant):
        """Open a quarantine case over one plant without changing its state."""
        return quarantine_stock(
            self.workspace, self.user, [{'type': 'plant', 'id': plant.pk}],
        )

    def create_order(self, status=SalesOrder.Status.DRAFT):
        """Create one order through its public endpoint."""
        response = self.client.post(
            self.orders_url,
            {'status': status, 'notes': 'Counter order'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def seedling_line(self, order, plant, quantity=1):
        """Add a seedling line priced for one plant's variety."""
        return self.add_line(order, {
            'line_type': 'seedling',
            'variety': plant.batch.variety_id,
            'description': plant.batch.variety.name,
            'quantity': quantity,
        })

    def tray_line(self, order, tray, quantity=1):
        """Add a tray line for the inventory item one tray belongs to."""
        return self.add_line(order, {
            'line_type': 'unit',
            'item': tray.inventory_unit.item_id,
            'description': 'One tray',
            'quantity': quantity,
        })

    def add_line(self, order, values):
        """Add one order line with the shared commercial terms."""
        response = self.client.post(self.lines_url, {
            'order': order['pk'],
            'unit_price': '11.5000',
            'tax_rate': '15.0000',
            **values,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def preview(self, order, line, **selection):
        """Ask what one selection would resolve to, without writing anything."""
        return self.client.post(
            f"{self.orders_url}{order['pk']}/allocation-preview/",
            {'line': line['pk'], **selection},
            format='json',
        )

    def allocate(self, order, line, **selection):
        """Attach one selection to a line."""
        return self.client.post(
            f"{self.orders_url}{order['pk']}/allocate/",
            {'line': line['pk'], **selection},
            format='json',
        )

    def reserved_elsewhere(self, plant):
        """Confirm another order holding this plant, so ours cannot have it."""
        other = self.create_order()
        line = self.seedling_line(other, plant)
        self.assertEqual(
            self.allocate(other, line, plant_ids=[plant.pk]).status_code,
            201,
        )
        confirmed = self.client.post(
            f"{self.orders_url}{other['pk']}/confirm/",
            {},
            format='json',
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)


class SeedlingSelectionTests(SelectionFixture):
    """Each refusal a seedling selection can carry is reachable and honoured."""

    def conflicted_plant(self, reason, line_plant):
        """Return a plant the preview should refuse for exactly one reason."""
        if reason == 'wrong_variety':
            return make_specific_plant(workspace=self.workspace).pk
        if reason == 'not_sellable':
            return self.plant().pk
        if reason == 'quarantined':
            plant = self.ready_plant()
            self.quarantine(plant)
            return plant.pk
        if reason == 'already_reserved':
            plant = self.ready_plant()
            self.reserved_elsewhere(plant)
            return plant.pk
        if reason == 'wrong_workspace':
            plant = self.ready_plant()
            # `workspace` is not editable, and building the whole tray, cell,
            # and planting chain elsewhere would test the factories rather
            # than the guard. Moving one row is the state being defended.
            SpecificPlant.objects.filter(pk=plant.pk).update(
                workspace=Workspace.objects.create(name='Other nursery'),
            )
            return plant.pk
        self.assertEqual(reason, 'unknown')
        return line_plant.pk + 10_000

    def test_the_declared_reasons_are_the_ones_the_code_can_produce(self):
        """A reason added without a case here would go unexercised."""
        self.assertEqual(
            set(SEEDLING_REASONS) | set(TRAY_REASONS) | set(LOT_REASONS) | set(COHORT_REASONS),
            declared_conflict_reasons(),
        )

    def test_every_refusal_is_reachable_and_allocate_refuses_it_too(self):
        """The preview is only worth reading if allocation agrees with it."""
        for reason in SEEDLING_REASONS:
            with self.subTest(reason=reason):
                wanted = self.ready_plant()
                order = self.create_order()
                line = self.seedling_line(order, wanted)
                refused = self.conflicted_plant(reason, wanted)

                preview = self.preview(order, line, plant_ids=[refused])
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertEqual(preview.data['selected'], [])
                self.assertEqual(preview.data['conflicts'][0]['id'], refused)
                self.assertEqual(preview.data['conflicts'][0]['reason'], reason)
                if reason == 'already_reserved':
                    self.assertIn('order_number', preview.data['conflicts'][0])

                allocated = self.allocate(order, line, plant_ids=[refused])
                self.assertEqual(allocated.status_code, 400, allocated.data)

    def test_a_mixed_selection_separates_what_can_be_had_from_what_cannot(self):
        """One preview answers for a whole basket, not the first bad row."""
        wanted = self.ready_plant()
        spare = self.ready_plant()
        held = self.ready_plant()
        self.reserved_elsewhere(held)
        order = self.create_order()
        line = self.seedling_line(order, wanted, quantity=2)

        preview = self.preview(
            order,
            line,
            plant_ids=[wanted.pk, held.pk, spare.pk],
        )

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(
            sorted(preview.data['selected']),
            sorted([wanted.pk, spare.pk]),
        )
        self.assertEqual(preview.data['conflicts'][0]['id'], held.pk)
        self.assertEqual(preview.data['conflicts'][0]['reason'], 'already_reserved')
        self.assertIn('order_number', preview.data['conflicts'][0])
        self.assertEqual(
            self.allocate(
                order, line, plant_ids=preview.data['selected'],
            ).status_code,
            201,
        )

    def test_a_line_already_holding_a_plant_does_not_refuse_its_own_hold(self):
        """Re-previewing a confirmed line must not report it against itself."""
        plant = self.ready_plant()
        order = self.create_order()
        line = self.seedling_line(order, plant)
        self.assertEqual(
            self.allocate(order, line, plant_ids=[plant.pk]).status_code, 201,
        )
        self.assertEqual(
            self.client.post(
                f"{self.orders_url}{order['pk']}/confirm/", {}, format='json',
            ).status_code,
            200,
        )

        preview = self.preview(order, line, plant_ids=[plant.pk])

        self.assertEqual(preview.data['selected'], [plant.pk])


class TraySelectionTests(SelectionFixture):
    """A tray line selects serialized units and refuses everything else."""

    def test_a_unit_of_another_item_or_out_of_stock_is_refused(self):
        """Both tray refusals are reachable and allocation agrees with them."""
        tray = make_seed_tray(workspace=self.workspace)
        cases = {
            'wrong_item': make_seed_tray(
                workspace=self.workspace,
            ).inventory_unit_id,
            'not_available': self.dispatched_unit(tray),
        }
        self.assertEqual(tuple(cases), TRAY_REASONS)
        for reason, unit_id in cases.items():
            with self.subTest(reason=reason):
                order = self.create_order()
                line = self.tray_line(order, tray)

                preview = self.preview(order, line, unit_ids=[unit_id])
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertEqual(
                    preview.data['conflicts'],
                    [{'id': unit_id, 'reason': reason}],
                )
                self.assertEqual(
                    self.allocate(order, line, unit_ids=[unit_id]).status_code,
                    400,
                )

    def dispatched_unit(self, like):
        """Return a tray unit of the line's own item that has left the nursery."""
        tray = make_seed_tray(workspace=self.workspace, model=like.model)
        InventoryUnit.objects.filter(pk=tray.inventory_unit_id).update(
            current_location=None,
        )
        return tray.inventory_unit_id

    def test_neither_line_type_accepts_the_other_kind_of_target(self):
        """A plant is not a tray, and the mix-up is caught before any lock."""
        plant = self.ready_plant()
        tray = make_seed_tray(workspace=self.workspace)
        order = self.create_order()
        seedling = self.seedling_line(order, plant)
        trays = self.tray_line(order, tray)

        crossed = (
            (seedling, {'unit_ids': [tray.inventory_unit_id]}, 'units'),
            (trays, {'plant_ids': [plant.pk]}, 'plants'),
        )
        for line, selection, field in crossed:
            with self.subTest(field=field):
                response = self.preview(order, line, **selection)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)

    def test_a_selection_names_exactly_one_source(self):
        """Ambiguity between IDs and filters is refused rather than ranked."""
        plant = self.ready_plant()
        order = self.create_order()
        line = self.seedling_line(order, plant)
        for label, selection in (
                ('none', {}),
                ('ids and filters', {
                    'plant_ids': [plant.pk],
                    'filters': {'sellable': 'true'},
                }),
        ):
            with self.subTest(selection=label):
                response = self.preview(order, line, **selection)
                self.assertEqual(response.status_code, 400, response.data)


class RegisterFilterSelectionTests(SelectionFixture):
    """Filters stand in for a list of IDs the operator never has to type."""

    def test_a_filter_selects_the_plants_that_match_it(self):
        """The register and the preview answer the same question the same way."""
        wanted = self.ready_plant()
        self.plant()
        order = self.create_order()
        line = self.seedling_line(order, wanted)

        preview = self.preview(order, line, filters={'sellable': 'true'})

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], [wanted.pk])

    def test_the_lines_variety_overrides_any_variety_the_caller_asked_for(self):
        """A filter cannot be used to select stock the line did not sell."""
        wanted = self.ready_plant()
        other = make_specific_plant(workspace=self.workspace)
        record_germination_event(other, self.user)
        record_lifecycle_event(other, self.user, OutcomeRequest(EventType.READY))
        order = self.create_order()
        line = self.seedling_line(order, wanted)

        preview = self.preview(
            order,
            line,
            filters={'variety': str(other.batch.variety_id)},
        )

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], [wanted.pk])

    def test_a_tray_line_cannot_be_filled_from_the_plant_register(self):
        """The register holds plants; a tray line is asking for something else."""
        tray = make_seed_tray(workspace=self.workspace)
        order = self.create_order()
        line = self.tray_line(order, tray)

        response = self.preview(order, line, filters={'sellable': 'true'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('filters', response.data)

    def test_a_filter_the_register_rejects_is_reported_by_its_own_name(self):
        """A malformed filter is the caller's to fix, not a server error."""
        plant = self.ready_plant()
        order = self.create_order()
        line = self.seedling_line(order, plant)

        response = self.preview(order, line, filters={'sellable': 'yes'})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('sellable', response.data)


class CohortSelectionTests(SelectionFixture):
    """Every refusal a cohort draw can carry is reachable and honoured."""

    def setUp(self):
        """Offer one block of anonymous seedlings for sale."""
        super().setUp()
        self.batch = make_production_batch()
        self.cohort = self.ready_cohort()

    def ready_cohort(self, quantity=100, batch=None):
        """Observe one block and put it on sale, as the register screen does."""
        cohort, _observed = observe_cohort(
            get_current_workspace(), self.user,
            batch=batch or self.batch, quantity=quantity,
            idempotency_key=uuid4(),
        )
        cohort, _ready = change_cohort(
            get_current_workspace(), self.user,
            cohort_id=cohort.pk, expected_revision=cohort.revision,
            action=CohortOperation.Action.READY, idempotency_key=uuid4(),
        )
        return cohort

    def cohort_line(self, order, quantity=10):
        """Add a cohort line for the fixture block's own variety."""
        return self.add_line(order, {
            'line_type': 'cohort_quantity',
            'variety': self.batch.variety_id,
            'description': 'Anonymous seedlings',
            'quantity': quantity,
        })

    def draw(self, cohort, revision=None, quantity=10):
        """Return the request body one draw on one block is posted as."""
        return {
            'cohort': cohort.pk,
            'quantity': quantity,
            'expected_revision': cohort.revision if revision is None else revision,
        }

    def growing_cohort(self):
        """Observe a block nobody has offered for sale yet."""
        cohort, _observed = observe_cohort(
            get_current_workspace(), self.user,
            batch=self.batch, quantity=10, idempotency_key=uuid4(),
        )
        return cohort

    def foreign_cohort(self):
        """Move one block into another workspace without rebuilding its graph."""
        cohort = self.ready_cohort()
        PlantCohort.objects.filter(pk=cohort.pk).update(
            workspace=Workspace.objects.create(name='Other nursery'),
        )
        return cohort

    def quarantined_cohort(self):
        """Offer a block for sale and then hold it back on health grounds."""
        cohort = self.ready_cohort()
        quarantine_stock(
            self.workspace, self.user, [{'type': 'cohort', 'id': cohort.pk}],
        )
        return cohort

    def conflicted_draw(self, reason):
        """Return a draw the preview should refuse for exactly one reason."""
        builders = {
            'wrong_variety': lambda: self.draw(self.ready_cohort(batch=make_production_batch())),
            'not_sellable': lambda: self.draw(self.growing_cohort()),
            'quarantined': lambda: self.draw(self.quarantined_cohort()),
            'stale_revision': lambda: self.draw(self.cohort, revision=self.cohort.revision + 1),
            'insufficient_stock': lambda: self.draw(self.ready_cohort(quantity=4)),
            'wrong_workspace': lambda: self.draw(self.foreign_cohort()),
            'unknown': lambda: {
                'cohort': self.cohort.pk + 10_000, 'quantity': 10, 'expected_revision': 1,
            },
        }
        return builders[reason]()

    def test_every_refusal_is_reachable_and_allocate_refuses_it_too(self):
        """The preview is only worth reading if allocation agrees with it."""
        for reason in COHORT_REASONS + SHARED_COHORT_REASONS:
            with self.subTest(reason=reason):
                order = self.create_order()
                line = self.cohort_line(order)
                refused = self.conflicted_draw(reason)

                preview = self.preview(order, line, cohort_requests=[refused])
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertEqual(preview.data['selected'], [])
                self.assertEqual(preview.data['conflicts'][0]['reason'], reason)

                allocated = self.allocate(order, line, cohort_requests=[refused])
                self.assertEqual(allocated.status_code, 400, allocated.data)

    def test_a_draw_that_fits_is_previewed_and_then_promised(self):
        """The revision travels with the draw, so the block cannot move under it."""
        order = self.create_order()
        line = self.cohort_line(order)
        draw = self.draw(self.cohort)

        preview = self.preview(order, line, cohort_requests=[draw])
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['selected'], [{
            'id': self.cohort.pk,
            'quantity': 10,
            'expected_revision': self.cohort.revision,
            'available': '100',
        }])

        allocated = self.allocate(order, line, cohort_requests=[draw])
        self.assertEqual(allocated.status_code, 201, allocated.data)
        self.assertEqual(allocated.data[0]['plant_cohort'], self.cohort.pk)
        self.assertEqual(allocated.data[0]['quantity'], 10)
