"""Protect the classification a GST return needs and a tax rate cannot give.

A rate of zero is a zero-rated export, an exempt supply, or something outside
GST altogether, and a return reports the first separately from the other two.
These tests cover the derivation on the way in, the snapshot onto the posted
records that a return is built from, and the narrow audited correction path
for lines whose order is already confirmed.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from inventory.models import InventoryItem
from inventory.units import UnitCode
from sales.models import FulfillmentLine, RefundLine, SalesOrder, SalesOrderLine
from sales.services import create_order
from tests.api import RESTContractTestCase
from tests.factories import make_inventory_item
from workspaces.models import Workspace, get_current_workspace

from .models import TaxTreatmentCorrection
from .services import correct_tax_treatment


class TaxTreatmentTestCase(RESTContractTestCase):
    """A Nursery workspace with an order that can still take lines."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.save()
        self.order = create_order(self.workspace, self.user, status=SalesOrder.Status.DRAFT)
        self.item = make_inventory_item(
            workspace=self.workspace,
            category=InventoryItem.Category.TRAY,
            tracking_mode=InventoryItem.TrackingMode.SERIALIZED,
            base_unit=UnitCode.EACH,
        )

    def add_line(self, **overrides):
        """Add one tray line to the draft order."""
        values = {
            'order': self.order,
            'line_type': SalesOrderLine.LineType.UNIT,
            'item': self.item,
            'description': 'Propagation trays',
            'quantity': 2,
            'unit_price': Decimal('10.0000'),
            'tax_rate': Decimal('15'),
            'discount_type': SalesOrderLine.DiscountType.NONE,
        }
        values.update(overrides)
        return SalesOrderLine.objects.create(**values)


class DerivationTests(TaxTreatmentTestCase):
    """What the rate already establishes is derived; the rest is left open."""

    def test_a_rated_line_is_standard_rated(self):
        """A rate above zero is a standard-rated supply by definition."""
        line = self.add_line()
        self.assertEqual(line.tax_treatment, SalesOrderLine.TaxTreatment.STANDARD)

    def test_an_unrated_line_stays_unclassified(self):
        """Calling it zero-rated would put it in a return box nobody chose."""
        line = self.add_line(tax_rate=Decimal('0'))
        self.assertEqual(line.tax_treatment, SalesOrderLine.TaxTreatment.UNCLASSIFIED)

    def test_an_explicit_treatment_is_kept(self):
        """Derivation fills a gap; it must never overrule an answer."""
        line = self.add_line(
            tax_rate=Decimal('0'),
            tax_treatment=SalesOrderLine.TaxTreatment.ZERO_RATED,
        )
        self.assertEqual(line.tax_treatment, SalesOrderLine.TaxTreatment.ZERO_RATED)


class ValidationTests(TaxTreatmentTestCase):
    """A treatment the rate contradicts would fill the wrong box of a return."""

    def test_a_standard_rated_line_needs_a_rate(self):
        """Standard-rated at zero percent is a contradiction, not a zero-rating."""
        with self.assertRaises(ValidationError) as caught:
            self.add_line(tax_rate=Decimal('0'), tax_treatment='standard')
        self.assertIn('tax_treatment', caught.exception.message_dict)

    def test_a_zero_rated_line_cannot_carry_a_rate(self):
        """Otherwise a 15% supply would be reported as a zero-rated export."""
        for treatment in ('zero_rated', 'exempt', 'out_of_scope', 'unclassified'):
            with self.subTest(treatment=treatment):
                with self.assertRaises(ValidationError) as caught:
                    self.add_line(tax_rate=Decimal('15'), tax_treatment=treatment)
                self.assertIn('tax_treatment', caught.exception.message_dict)

    def test_the_database_refuses_a_contradiction_too(self):
        """bulk_create skips validation; the constraint is what is left."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesOrderLine.objects.bulk_create([SalesOrderLine(
                order=self.order,
                line_type=SalesOrderLine.LineType.UNIT,
                item=self.item,
                description='Trays',
                quantity=1,
                unit_price=Decimal('10.0000'),
                tax_rate=Decimal('15'),
                tax_treatment='zero_rated',
            )])

    def test_the_database_refuses_a_blank_treatment(self):
        """A blank would be counted in no box at all and reported nowhere."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesOrderLine.objects.bulk_create([SalesOrderLine(
                order=self.order,
                line_type=SalesOrderLine.LineType.UNIT,
                item=self.item,
                description='Trays',
                quantity=1,
                unit_price=Decimal('10.0000'),
                tax_rate=Decimal('15'),
                tax_treatment='',
            )])


class CorrectionTests(TaxTreatmentTestCase):
    """Reclassifying a confirmed line moves no money, and is audited anyway."""

    def confirmed_zero_rated_line(self):
        """A zero-rated-looking line on an order that is no longer editable."""
        line = self.add_line(tax_rate=Decimal('0'))
        SalesOrder.objects.filter(pk=self.order.pk).update(
            status=SalesOrder.Status.CONFIRMED,
        )
        self.order.refresh_from_db()
        line.refresh_from_db()
        return line

    def test_a_confirmed_line_cannot_be_reclassified_by_saving_it(self):
        """This is the guard the correction path exists to work around."""
        line = self.confirmed_zero_rated_line()
        line.tax_treatment = SalesOrderLine.TaxTreatment.ZERO_RATED
        with self.assertRaises(ValidationError) as caught:
            line.save()
        self.assertIn('order', caught.exception.message_dict)

    def test_the_correction_service_reclassifies_it(self):
        """Without this, the zero-rated box is empty forever on any history."""
        line = self.confirmed_zero_rated_line()
        correct_tax_treatment(line, 'zero_rated', self.user, 'Export to Australia')
        line.refresh_from_db()
        self.assertEqual(line.tax_treatment, SalesOrderLine.TaxTreatment.ZERO_RATED)

    def test_the_correction_is_recorded_with_its_actor_and_reason(self):
        """A reclassification changes a filed figure; who and why are the record."""
        line = self.confirmed_zero_rated_line()
        correction = correct_tax_treatment(line, 'exempt', self.user, 'Residential rent')
        self.assertEqual(correction.previous_treatment, 'unclassified')
        self.assertEqual(correction.treatment, 'exempt')
        self.assertEqual(correction.created_by, self.user)
        self.assertEqual(correction.reason, 'Residential rent')
        self.assertEqual(correction.workspace, self.workspace)

    def test_a_correction_is_immutable(self):
        """The audit trail is worthless if it can be edited afterwards."""
        line = self.confirmed_zero_rated_line()
        correction = correct_tax_treatment(line, 'exempt', self.user, 'Residential rent')
        correction.treatment = 'zero_rated'
        with self.assertRaises(ValidationError):
            correction.save()
        with self.assertRaises(ValidationError):
            correction.delete()

    def test_a_rated_line_cannot_be_corrected(self):
        """Its treatment is part of the price, which is not ours to change."""
        line = self.add_line(tax_rate=Decimal('15'))
        with self.assertRaises(ValidationError) as caught:
            correct_tax_treatment(line, 'zero_rated', self.user, 'Wrong')
        self.assertIn('sales_order_line', caught.exception.message_dict)

    def test_correcting_to_standard_rated_is_refused(self):
        """That would add tax to an agreed price, which is a different act."""
        line = self.confirmed_zero_rated_line()
        with self.assertRaises(ValidationError) as caught:
            correct_tax_treatment(line, 'standard', self.user, 'Wrong')
        self.assertIn('treatment', caught.exception.message_dict)

    def test_a_correction_that_changes_nothing_is_refused(self):
        """An audit row recording no change is noise in the trail."""
        line = self.confirmed_zero_rated_line()
        correct_tax_treatment(line, 'zero_rated', self.user, 'Export')
        line.refresh_from_db()
        with self.assertRaises(ValidationError) as caught:
            correct_tax_treatment(line, 'zero_rated', self.user, 'Export')
        self.assertIn('treatment', caught.exception.message_dict)

    def test_no_correction_row_is_left_behind_when_the_change_is_refused(self):
        """A rolled-back correction must not leave the audit trail lying."""
        line = self.add_line(tax_rate=Decimal('15'))
        with self.assertRaises(ValidationError):
            correct_tax_treatment(line, 'zero_rated', self.user, 'Wrong')
        self.assertFalse(TaxTreatmentCorrection.objects.exists())


class PostedSnapshotTests(TaxTreatmentTestCase):
    """The posted records are the record of record for a return."""

    def test_the_snapshot_fields_exist_on_both_posted_records(self):
        """A return reads these, not the order line, so both must carry it."""
        for model in (FulfillmentLine, RefundLine):
            with self.subTest(model=model.__name__):
                self.assertIn(
                    'tax_treatment',
                    [field.name for field in model._meta.get_fields()],  # pylint: disable=protected-access
                )
