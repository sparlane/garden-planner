"""Invariants of the input application document and its parts."""

# pylint: disable=duplicate-code

from decimal import Decimal
from itertools import count

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from inventory.models import InventoryItem
from inventory.units import UnitCode
from tests.factories import (
    make_garden_square,
    make_inventory_item,
    make_location,
    make_production_batch,
    make_seed_tray,
    make_seed_tray_cell,
    make_stock_lot,
)
from workspaces.models import Workspace

from .models import (
    TARGET_FIELDS,
    InputApplication,
    InputApplicationLine,
    InputApplicationTarget,
)


_WORKSPACES = count(1)


def _next_workspace_name():
    """Name each extra workspace distinctly within one test run."""
    return f'Other workspace {next(_WORKSPACES)}'


class ApplicationFixtureTestCase(TestCase):
    """Shared draft, line, and target builders."""

    def setUp(self):
        """Stock one lot at one location for every case to draw on."""
        super().setUp()
        self.location = make_location()
        self.item = make_inventory_item()
        self.lot = make_stock_lot(item=self.item, location=self.location, quantity='50')

    def make_application(self, **overrides):
        """Create a draft application drawing from the shared location."""
        values = {
            'applied_at': timezone.now(),
            'source_location': self.location,
        }
        values.update(overrides)
        return InputApplication.objects.create(**values)

    def make_line(self, application=None, **overrides):
        """Create a manual line consuming one litre from the shared lot."""
        values = {
            'application': application or self.make_application(),
            'item': self.item,
            'lot': self.lot,
            'usage_basis': InventoryItem.UsageBasis.MANUAL,
            'base_unit': self.item.base_unit,
            'applied_quantity': Decimal('1'),
            'unit_code': UnitCode.LITRE,
            'applied_base_quantity': Decimal('1'),
        }
        values.update(overrides)
        return InputApplicationLine.objects.create(**values)

    def make_foreign_draft(self):
        """Create a draft in a second workspace with stock of its own.

        Tests point one of these at this workspace's things, which is the
        direction a cross-workspace mistake actually arrives from.
        """
        other = Workspace.objects.create(name=_next_workspace_name())
        location = make_location(workspace=other)
        item = make_inventory_item(workspace=other)
        lot = make_stock_lot(item=item, location=location)
        application = InputApplication.objects.create(
            workspace=other,
            applied_at=timezone.now(),
            source_location=location,
        )
        return application, item, lot

    def make_target(self, line=None, **overrides):
        """Create a batch target for one line."""
        values = {
            'line': line or self.make_line(),
            'target_type': InputApplicationTarget.TargetType.BATCH,
        }
        values.update(overrides)
        if values['target_type'] == InputApplicationTarget.TargetType.BATCH:
            values.setdefault('batch', make_production_batch())
        return InputApplicationTarget.objects.create(**values)


class InputApplicationTests(ApplicationFixtureTestCase):
    """Rules governing the document header."""

    def test_an_application_starts_as_a_draft(self):
        """Nothing decrements stock until an operator posts the document."""
        application = self.make_application()
        self.assertEqual(application.status, InputApplication.Status.DRAFT)
        self.assertIsNone(application.posted_at)

    def test_an_application_cannot_be_created_already_posted(self):
        """Posting is a service that writes movements, not a starting state."""
        with self.assertRaises(ValidationError):
            self.make_application(status=InputApplication.Status.POSTED)

    def test_a_posted_application_cannot_be_edited(self):
        """A mistake is reversed, which keeps the original readable."""
        application = self.make_application()
        InputApplication.objects.filter(pk=application.pk).update(
            status=InputApplication.Status.POSTED,
            posted_at=timezone.now(),
        )
        application.refresh_from_db()
        application.notes = 'Changed my mind'
        with self.assertRaises(ValidationError):
            application.save()

    def test_only_a_draft_application_can_be_deleted(self):
        """A posted document stays on file as the record of what was used."""
        application = self.make_application()
        InputApplication.objects.filter(pk=application.pk).update(
            status=InputApplication.Status.POSTED,
            posted_at=timezone.now(),
        )
        application.refresh_from_db()
        with self.assertRaises(ValidationError):
            application.delete()

    def test_a_draft_application_can_be_deleted(self):
        """An abandoned draft leaves nothing behind."""
        application = self.make_application()
        application.delete()
        self.assertFalse(InputApplication.objects.filter(pk=application.pk).exists())

    def test_an_inactive_source_location_is_refused(self):
        """Stock cannot be drawn from a location that is out of service."""
        retired = make_location(active=False)
        with self.assertRaises(ValidationError) as caught:
            self.make_application(source_location=retired)
        self.assertIn('source_location', caught.exception.message_dict)

    def test_a_batch_from_another_workspace_is_refused(self):
        """A document cannot attribute inputs to another workspace's crop."""
        other = Workspace.objects.create(name=_next_workspace_name())
        with self.assertRaises(ValidationError) as caught:
            InputApplication.objects.create(
                workspace=other,
                applied_at=timezone.now(),
                source_location=make_location(workspace=other),
                batch=make_production_batch(),
            )
        self.assertIn('batch', caught.exception.message_dict)

    def test_a_reversed_stamp_requires_a_posting(self):
        """The database refuses a reversal that never posted."""
        application = self.make_application()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InputApplication.objects.filter(pk=application.pk).update(
                status=InputApplication.Status.REVERSED,
                reversed_at=timezone.now(),
            )


class InputApplicationLineTests(ApplicationFixtureTestCase):
    """Rules governing one item drawn from one exact lot."""

    def test_a_line_records_the_lot_it_drew_from(self):
        """Attribution needs the exact lot, not just the item."""
        line = self.make_line()
        self.assertEqual(line.lot, self.lot)

    def test_a_lot_of_a_different_item_is_refused(self):
        """A line cannot claim stock that is not the item it names."""
        other_item = make_inventory_item()
        other_lot = make_stock_lot(item=other_item, location=self.location)
        with self.assertRaises(ValidationError) as caught:
            self.make_line(lot=other_lot)
        self.assertIn('lot', caught.exception.message_dict)

    def test_exactly_one_display_unit_is_required(self):
        """A quantity is meaningless without exactly one unit to read it in."""
        with self.assertRaises(ValidationError) as caught:
            self.make_line(unit_code=None)
        self.assertIn('unit_code', caught.exception.message_dict)

    def test_the_base_unit_snapshot_must_match_the_item(self):
        """A wrong snapshot would misread every quantity on the line."""
        with self.assertRaises(ValidationError) as caught:
            self.make_line(base_unit=UnitCode.GRAM)
        self.assertIn('base_unit', caught.exception.message_dict)

    def test_a_zero_applied_quantity_is_refused(self):
        """Applying nothing is not an application."""
        with self.assertRaises(ValidationError):
            self.make_line(
                applied_quantity=Decimal('0'),
                applied_base_quantity=Decimal('0'),
            )

    def test_negative_waste_is_refused(self):
        """Waste is an amount discarded, never an amount recovered."""
        with self.assertRaises(ValidationError):
            self.make_line(waste_base_quantity=Decimal('-1'))

    def test_the_database_refuses_a_non_positive_applied_quantity(self):
        """The check constraint holds when validation is bypassed."""
        line = self.make_line()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InputApplicationLine.objects.filter(pk=line.pk).update(
                applied_base_quantity=Decimal('0'),
            )

    def test_a_posted_line_cannot_be_edited(self):
        """The consumed amount is fixed once it has moved stock."""
        line = self.make_line()
        InputApplication.objects.filter(pk=line.application_id).update(
            status=InputApplication.Status.POSTED,
            posted_at=timezone.now(),
        )
        line.refresh_from_db()
        line.notes = 'Actually it was more'
        with self.assertRaises(ValidationError):
            line.save()

    def test_an_item_from_another_workspace_is_refused(self):
        """A document cannot consume another workspace's stock."""
        other = Workspace.objects.create(name=_next_workspace_name())
        item = make_inventory_item(workspace=other)
        with self.assertRaises(ValidationError) as caught:
            self.make_line(item=item)
        self.assertIn('item', caught.exception.message_dict)


class InputApplicationTargetTests(ApplicationFixtureTestCase):
    """Rules governing what a line was applied to."""

    def test_target_fields_match_the_declared_choices(self):
        """The generated constraints cover every supported target exactly once."""
        self.assertEqual(
            tuple(InputApplicationTarget.TargetType.values),
            TARGET_FIELDS,
        )

    def test_a_target_resolves_to_the_thing_it_points_at(self):
        """One accessor reads whichever column the type selected."""
        batch = make_production_batch()
        target = self.make_target(batch=batch)
        self.assertEqual(target.target, batch)
        self.assertEqual(target.target_id, batch.pk)

    def test_a_target_needs_exactly_one_thing(self):
        """Two targets on one row would make the weight ambiguous."""
        line = self.make_line()
        with self.assertRaises(ValidationError) as caught:
            InputApplicationTarget.objects.create(
                line=line,
                target_type=InputApplicationTarget.TargetType.BATCH,
                batch=make_production_batch(),
                garden_square=make_garden_square(),
            )
        self.assertIn('target_type', caught.exception.message_dict)

    def test_a_target_must_match_its_declared_type(self):
        """The declared type is what every reader dispatches on."""
        line = self.make_line()
        with self.assertRaises(ValidationError) as caught:
            InputApplicationTarget.objects.create(
                line=line,
                target_type=InputApplicationTarget.TargetType.GARDEN_SQUARE,
                batch=make_production_batch(),
            )
        self.assertIn('target_type', caught.exception.message_dict)

    def test_no_target_at_all_is_refused(self):
        """A weight with nothing to weigh cannot contribute to a calculation."""
        line = self.make_line()
        with self.assertRaises(ValidationError):
            InputApplicationTarget.objects.create(
                line=line,
                target_type=InputApplicationTarget.TargetType.BATCH,
            )

    def test_the_database_refuses_a_mismatched_target(self):
        """The identity constraint holds when validation is bypassed."""
        target = self.make_target()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InputApplicationTarget.objects.filter(pk=target.pk).update(
                target_type=InputApplicationTarget.TargetType.GARDEN_ROW,
            )

    def test_the_same_target_cannot_be_listed_twice_on_one_line(self):
        """A whole-tray shortcut must not double count a hand-picked cell."""
        tray = make_seed_tray()
        cell = make_seed_tray_cell(tray=tray)
        line = self.make_line()
        self.make_target(
            line=line,
            target_type=InputApplicationTarget.TargetType.SEED_TRAY_CELL,
            seed_tray_cell=cell,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            InputApplicationTarget.objects.bulk_create([
                InputApplicationTarget(
                    line=line,
                    target_type=InputApplicationTarget.TargetType.SEED_TRAY_CELL,
                    seed_tray_cell=cell,
                ),
            ])

    def test_the_same_target_may_appear_on_different_lines(self):
        """One tray cell can receive both media and a treatment."""
        tray = make_seed_tray()
        cell = make_seed_tray_cell(tray=tray)
        application = self.make_application()
        for _ in range(2):
            line = self.make_line(application=application)
            self.make_target(
                line=line,
                target_type=InputApplicationTarget.TargetType.SEED_TRAY_CELL,
                seed_tray_cell=cell,
            )
        self.assertEqual(
            InputApplicationTarget.objects.filter(seed_tray_cell=cell).count(),
            2,
        )

    def test_a_zero_weight_is_refused(self):
        """A target that received none of the input is not a target."""
        with self.assertRaises(ValidationError):
            self.make_target(weight=Decimal('0'))

    def test_a_tray_cell_from_another_workspace_is_refused(self):
        """Cells are reached through their tray, which carries the ownership."""
        application, item, lot = self.make_foreign_draft()
        line = self.make_line(application=application, item=item, lot=lot)
        cell = make_seed_tray_cell(tray=make_seed_tray())
        with self.assertRaises(ValidationError) as caught:
            self.make_target(
                line=line,
                target_type=InputApplicationTarget.TargetType.SEED_TRAY_CELL,
                seed_tray_cell=cell,
            )
        self.assertIn('target_type', caught.exception.message_dict)

    def test_a_posted_target_cannot_be_edited(self):
        """The snapshot is what proves the calculation was not rewritten."""
        target = self.make_target()
        InputApplication.objects.filter(
            pk=target.line.application_id,
        ).update(
            status=InputApplication.Status.POSTED,
            posted_at=timezone.now(),
        )
        target.refresh_from_db()
        target.weight = Decimal('0.5')
        with self.assertRaises(ValidationError):
            target.save()
