"""The information a document must state, band by band.

This is change 5's matrix, and it runs without a database because
`thresholds` holds no database access — the same split that makes
`tax.recognition` testable as arithmetic rather than as fixtures.
"""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from .thresholds import (
    BUYER_IDENTIFICATION,
    BUYER_NAME,
    DOCUMENT_DATE,
    FULL,
    GST_STATED,
    LOW,
    QUANTITY,
    SELLER_GST_NUMBER,
    SELLER_NAME,
    STANDARD,
    SUPPLY_DESCRIPTION,
    TOTAL_PAYABLE,
    DocumentInformation,
    describe,
    missing_information,
    required_elements,
    tier_for,
)


def complete(**overrides):
    """Return a document stating everything, so a test can remove one thing."""
    values = {
        'total_incl_tax': Decimal('1500.0000'),
        'taxable_supply': True,
        'seller_name': 'Kowhai Growers Limited',
        'seller_gst_number': '049091850',
        'document_date': date(2026, 5, 4),
        'gst_stated': True,
        'line_descriptions': ('Hebe "Wiri Charm" 2L',),
        'supply_quantities': (Decimal('6'),),
        'buyer_name': 'Riverbend Landscapes',
        'buyer_identification': '8 Quarry Road, Brightwater',
    }
    values.update(overrides)
    return DocumentInformation(**values)


class TierBoundaryTests(SimpleTestCase):
    """Each band is named by an inclusive upper bound, not a rounded one."""

    def test_two_hundred_exactly_is_the_low_band(self):
        """A supply *of* $200 is $200 or less; only more than it is not."""
        self.assertEqual(tier_for(Decimal('0')), LOW)
        self.assertEqual(tier_for(Decimal('200.0000')), LOW)
        self.assertEqual(tier_for(Decimal('200.0001')), STANDARD)

    def test_a_thousand_exactly_is_the_middle_band(self):
        """The same inclusive rule applies at the upper boundary."""
        self.assertEqual(tier_for(Decimal('1000.0000')), STANDARD)
        self.assertEqual(tier_for(Decimal('1000.0001')), FULL)


class RequiredElementTests(SimpleTestCase):
    """What each band asks for, stated once and read back."""

    def test_every_band_needs_the_seller_date_description_and_total(self):
        """The floor is the same whatever the supply is worth."""
        base = {SELLER_NAME, SELLER_GST_NUMBER, DOCUMENT_DATE, SUPPLY_DESCRIPTION, TOTAL_PAYABLE, GST_STATED}
        for tier in (LOW, STANDARD, FULL):
            with self.subTest(tier=tier):
                self.assertTrue(base.issubset(set(required_elements(tier))))

    def test_quantity_starts_above_two_hundred(self):
        """A punnet over the counter needs no volume stated; a pallet does."""
        self.assertNotIn(QUANTITY, required_elements(LOW))
        self.assertIn(QUANTITY, required_elements(STANDARD))
        self.assertIn(QUANTITY, required_elements(FULL))

    def test_the_buyer_is_only_identified_above_a_thousand(self):
        """Asking a walk-in for an address to buy a plant is the failure this avoids."""
        for tier in (LOW, STANDARD):
            with self.subTest(tier=tier):
                self.assertNotIn(BUYER_NAME, required_elements(tier))
                self.assertNotIn(BUYER_IDENTIFICATION, required_elements(tier))
        self.assertIn(BUYER_NAME, required_elements(FULL))
        self.assertIn(BUYER_IDENTIFICATION, required_elements(FULL))

    def test_an_unregistered_seller_is_asked_for_no_gst_information(self):
        """An absent GST number is correct there, not a missing element."""
        for tier in (LOW, STANDARD, FULL):
            with self.subTest(tier=tier):
                elements = required_elements(tier, taxable_supply=False)
                self.assertNotIn(SELLER_GST_NUMBER, elements)
                self.assertNotIn(GST_STATED, elements)
                self.assertIn(SELLER_NAME, elements)

    def test_an_unknown_band_is_refused_rather_than_defaulted(self):
        """Defaulting to the least demanding band would be the wrong direction."""
        with self.assertRaises(ValueError):
            required_elements('enormous')


class MissingInformationTests(SimpleTestCase):
    """A complete document reports nothing; each omission reports itself."""

    def test_a_complete_document_of_every_size_is_complete(self):
        """Totals of $50, $500 and $5,000 all satisfy their own band."""
        for total in ('50.0000', '500.0000', '5000.0000'):
            with self.subTest(total=total):
                self.assertEqual(missing_information(complete(total_incl_tax=Decimal(total))), ())

    def test_a_large_supply_without_a_buyer_names_both_buyer_elements(self):
        """The $1,500 walk-in is refused, saying exactly what it needs."""
        information = complete(buyer_name='  ', buyer_identification='')
        self.assertEqual(missing_information(information), (BUYER_NAME, BUYER_IDENTIFICATION))

    def test_the_same_supply_under_a_thousand_needs_no_buyer(self):
        """Only the value moved; the anonymous sale is fine."""
        information = complete(total_incl_tax=Decimal('900.0000'), buyer_name='', buyer_identification='')
        self.assertEqual(missing_information(information), ())

    def test_one_undescribed_line_makes_the_whole_document_undescribed(self):
        """A document is only as described as its least described line."""
        information = complete(line_descriptions=('Hebe "Wiri Charm" 2L', '   '))
        self.assertEqual(missing_information(information), (SUPPLY_DESCRIPTION,))

    def test_a_document_with_no_lines_describes_nothing(self):
        """An empty document states no supply, so it cannot describe one."""
        information = complete(line_descriptions=(), supply_quantities=())
        self.assertEqual(missing_information(information), (SUPPLY_DESCRIPTION,))

    def test_an_unquantified_supply_fails_only_above_two_hundred(self):
        """The band decides whether the omission matters."""
        unquantified = {'supply_quantities': (None,)}
        self.assertEqual(missing_information(complete(**unquantified)), (QUANTITY,))
        low = complete(total_incl_tax=Decimal('120.0000'), **unquantified)
        self.assertEqual(missing_information(low), ())

    def test_a_line_about_no_goods_is_not_asked_for_a_quantity(self):
        """A quantity is required of what supplies goods, not of every line."""
        information = complete(
            line_descriptions=('Delivery to Brightwater',),
            supply_quantities=(),
        )
        self.assertEqual(missing_information(information), ())

    def test_a_registered_seller_without_a_number_is_incomplete(self):
        """The registration says it is taxable supply information; prove it."""
        information = complete(seller_gst_number='')
        self.assertEqual(missing_information(information), (SELLER_GST_NUMBER,))

    def test_an_unregistered_seller_without_a_number_is_complete(self):
        """The same absence, correct this time, and reported as correct."""
        information = complete(taxable_supply=False, seller_gst_number='', gst_stated=False)
        self.assertEqual(missing_information(information), ())

    def test_a_missing_legal_name_is_reported_for_every_band(self):
        """Nothing substitutes the workspace name for the name of the entity."""
        for total in ('50.0000', '500.0000', '5000.0000'):
            with self.subTest(total=total):
                information = complete(total_incl_tax=Decimal(total), seller_name='')
                self.assertIn(SELLER_NAME, missing_information(information))

    def test_missing_elements_read_in_the_order_the_requirement_states_them(self):
        """A message reads the way the rule is written, not the way a set iterated."""
        information = complete(seller_name='', document_date=None, buyer_name='')
        self.assertEqual(missing_information(information), (SELLER_NAME, DOCUMENT_DATE, BUYER_NAME))

    def test_each_element_renders_as_readable_words(self):
        """The codes are for machines; a person is told what to go and enter."""
        self.assertEqual(
            describe((SELLER_NAME, BUYER_IDENTIFICATION)),
            'the name of the supplier, an address or other identifier for the recipient',
        )
