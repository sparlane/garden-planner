"""The record a converted transaction carries its rate in (task 121).

One workspace in New Zealand dollars, one supplier invoice in euros, and the
rate somebody typed against it. Everything here is about the record: what it
stores, what it refuses, and how a wrong rate is corrected without erasing the
one it replaces.
"""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring,duplicate-code

from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError

from purchasing.models import SupplierInvoice, SupplierPayment
from purchasing.services import create_invoice, record_supplier_payment
from tests.api import RESTContractTestCase
from tests.factories import make_confirmed_invoice, make_nursery_workspace, make_supplier
from workspaces.conversion import ConversionMethod, QuoteDirection

from .conversion import (
    backfill_identity_conversions,
    live_conversion,
    record_conversion,
)
from .models import CurrencyConversion


#: The rate the euro invoice below is converted at. Its 10.00 of goods come to
#: 16.7011, its 1.50 of tax to 2.5052 and its 11.50 gross to 19.2062 -- and the
#: first two add up to 19.2063, one cent away from the third. That divergence is
#: the point of converting each amount on its own, and it is stated where it
#: shows rather than pushed onto whichever line happens to be last.
EURO_RATE = Decimal('1.6701057193')


def rate_request(**overrides):
    """What an operator types when converting one transaction."""
    values = {
        'rate': EURO_RATE,
        'quote_direction': QuoteDirection.TARGET_PER_SOURCE,
        'method': ConversionMethod.SPOT,
        'rate_source': 'Reserve Bank of New Zealand, 1 August 2026',
        'effective_date': date(2026, 8, 1),
    }
    values.update(overrides)
    return values


class ConversionTestCase(RESTContractTestCase):
    """A New Zealand nursery with one euro payable and one of its own."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace(multi_currency_enabled=True)
        self.supplier = make_supplier()
        self.home_invoice = make_confirmed_invoice(
            self.workspace, self.user, self.supplier,
        )
        self.euro_invoice = make_confirmed_invoice(
            self.workspace, self.user, self.supplier, currency_code='EUR',
        )

    def amounts_of(self, conversion):
        """Return the converted figures by name, for comparing to a figure."""
        return {row['name']: row['converted'] for row in conversion.amounts}


class StoredConversionTests(ConversionTestCase):
    """Verification 2: a foreign source amount reconciles to a stated figure."""

    def test_the_rate_and_everything_needed_to_reproduce_it_are_stored(self):
        conversion = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )

        self.assertEqual(f'{conversion.rate:f}', '1.6701057193')
        self.assertEqual(conversion.quote_direction, 'target_per_source')
        self.assertEqual(conversion.method, 'spot')
        self.assertEqual(
            conversion.rate_source, 'Reserve Bank of New Zealand, 1 August 2026',
        )
        self.assertEqual(conversion.effective_date, date(2026, 8, 1))
        self.assertIsNotNone(conversion.converted_on)
        self.assertEqual(conversion.source_currency_code, 'EUR')
        self.assertEqual(conversion.target_currency_code, 'NZD')

    def test_every_amount_on_the_row_is_converted_on_its_own(self):
        conversion = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )

        self.assertEqual(self.amounts_of(conversion), {
            'subtotal_ex_tax': '16.7011',
            'tax_total': '2.5052',
            'total_incl_tax': '19.2062',
        })

    def test_the_converted_lines_need_not_add_up_to_the_converted_total(self):
        """The cent is left where the arithmetic put it, and said out loud."""
        conversion = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )
        amounts = self.amounts_of(conversion)

        parts = Decimal(amounts['subtotal_ex_tax']) + Decimal(amounts['tax_total'])
        self.assertEqual(f'{parts:.4f}', '19.2063')
        self.assertEqual(amounts['total_incl_tax'], '19.2062')

    def test_the_original_amount_is_kept_beside_what_it_converts_to(self):
        conversion = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )

        self.assertEqual(
            [row['original'] for row in conversion.amounts],
            ['10.0000', '1.5000', '11.5000'],
        )
        self.euro_invoice.refresh_from_db()
        self.assertEqual(self.euro_invoice.currency_code, 'EUR')
        self.assertEqual(f'{self.euro_invoice.total_incl_tax:.4f}', '11.5000')

    def test_a_rate_quoted_the_other_way_round_is_stored_as_such(self):
        conversion = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(
                rate=Decimal('0.5485'),
                quote_direction=QuoteDirection.SOURCE_PER_TARGET,
            ),
            self.user,
        )

        self.assertEqual(
            self.amounts_of(conversion)['subtotal_ex_tax'], '18.2315',
        )


class ConversionRefusalTests(ConversionTestCase):
    """What a conversion will not record, because it would be a wrong number."""

    def test_an_amount_already_in_the_workspace_currency_is_not_converted(self):
        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', self.home_invoice.pk,
                rate_request(), self.user,
            )

        self.assertIn('source_currency_code', refused.exception.message_dict)

    def test_a_method_the_workspace_does_not_convert_by_is_refused(self):
        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
                rate_request(method=ConversionMethod.PERIOD_END), self.user,
            )

        self.assertIn(
            'This workspace converts every foreign amount at the spot rate',
            refused.exception.message_dict['method'][0],
        )

    def test_a_record_whose_amounts_can_still_move_is_not_converted(self):
        draft = create_invoice(
            self.workspace, self.user,
            {
                'supplier': self.supplier, 'external_reference': 'DRAFT-1',
                'invoice_date': date(2026, 8, 1), 'currency_code': 'EUR',
            },
            [{
                'description': 'Seed order', 'subtotal_ex_tax': Decimal('10'),
                'tax_rate': Decimal('15'), 'tax_total': Decimal('1.5'),
                'total_incl_tax': Decimal('11.5'),
            }],
        )

        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', draft.pk,
                rate_request(), self.user,
            )

        self.assertEqual(
            refused.exception.message_dict['source_id'],
            ['Only a confirmed supplier invoice can be converted.'],
        )
        self.assertEqual(draft.status, SupplierInvoice.Status.DRAFT)

    def test_a_record_a_rate_cannot_be_recorded_against_says_which_can(self):
        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'cost_layer', 1, rate_request(), self.user,
            )

        self.assertIn(
            'cost_layer is not a record a rate is recorded against',
            refused.exception.message_dict['source_type'][0],
        )

    def test_a_rate_that_is_not_above_zero_is_refused(self):
        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
                rate_request(rate=Decimal('0')), self.user,
            )

        self.assertEqual(
            refused.exception.message_dict['rate'],
            ['A conversion rate must be above zero.'],
        )


class ConversionCorrectionTests(ConversionTestCase):
    """A wrong rate is replaced by a record, not by an edit."""

    def setUp(self):
        super().setUp()
        self.first = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )

    def test_a_second_rate_has_to_say_which_one_it_replaces(self):
        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
                rate_request(rate=Decimal('2')), self.user,
            )

        self.assertIn(
            'already converted', refused.exception.message_dict['supersedes'][0],
        )

    def test_the_correction_becomes_live_and_the_earlier_rate_stays_readable(self):
        corrected = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(
                rate=Decimal('2'), reason='The first rate was the wrong day.',
                supersedes=self.first.pk,
            ),
            self.user,
        )

        live = live_conversion(self.workspace, 'supplier_invoice', self.euro_invoice.pk)
        self.assertEqual(live.pk, corrected.pk)
        self.assertEqual(corrected.supersedes_id, self.first.pk)
        self.assertEqual(self.amounts_of(corrected)['total_incl_tax'], '23.0000')
        self.first.refresh_from_db()
        self.assertEqual(f'{self.first.rate:f}', '1.6701057193')
        self.assertEqual(self.amounts_of(self.first)['total_incl_tax'], '19.2062')

    def test_a_correction_cannot_replace_a_rate_that_is_not_the_live_one(self):
        corrected = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(rate=Decimal('2'), supersedes=self.first.pk), self.user,
        )

        with self.assertRaises(ValidationError) as refused:
            record_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
                rate_request(rate=Decimal('3'), supersedes=self.first.pk), self.user,
            )

        self.assertIn(
            'not the conversion this record is currently read at',
            refused.exception.message_dict['supersedes'][0],
        )
        self.assertEqual(
            live_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            ).pk,
            corrected.pk,
        )

    def test_a_recorded_conversion_is_neither_edited_nor_deleted(self):
        self.first.rate = Decimal('2')
        with self.assertRaises(ValidationError):
            self.first.save()
        with self.assertRaises(ValidationError):
            self.first.delete()


class IdentityBackfillTests(ConversionTestCase):
    """Verification 1: a row already in the base currency carries a rate of one."""

    def setUp(self):
        super().setUp()
        self.payment = record_supplier_payment(
            self.workspace, self.user,
            {
                'supplier': self.supplier, 'paid_on': date(2026, 8, 2),
                'amount': Decimal('11.5'), 'currency_code': 'NZD',
                'method': SupplierPayment.Method.BANK_TRANSFER,
            },
            [{'invoice': self.home_invoice, 'amount': Decimal('11.5')}],
        )

    def run_backfill(self):
        """Run the deployment migration's own function over the real rows."""
        migration = import_module('bookkeeping.migrations.0004_identity_conversions')
        migration.record_identity_conversions(django_apps, None)

    def test_the_backfill_converts_every_base_currency_row_at_a_rate_of_one(self):
        self.run_backfill()

        conversion = live_conversion(
            self.workspace, 'supplier_invoice', self.home_invoice.pk,
        )
        self.assertEqual(conversion.method, 'base_currency')
        self.assertEqual(f'{conversion.rate:f}', '1.0000000000')
        self.assertEqual(conversion.source_currency_code, 'NZD')
        self.assertEqual(conversion.effective_date, date(2026, 8, 1))
        self.assertEqual(self.amounts_of(conversion)['total_incl_tax'], '11.5000')
        self.assertIsNotNone(
            live_conversion(self.workspace, 'supplier_payment', self.payment.pk),
        )

    def test_a_foreign_row_is_left_for_somebody_to_type_a_rate_against(self):
        self.run_backfill()

        self.assertIsNone(
            live_conversion(self.workspace, 'supplier_invoice', self.euro_invoice.pk),
        )

    def test_running_it_again_records_no_second_rate_for_anything(self):
        self.run_backfill()
        before = CurrencyConversion.objects.count()

        backfill_identity_conversions(self.workspace)

        self.assertEqual(CurrencyConversion.objects.count(), before)

    def test_it_leaves_a_typed_rate_alone(self):
        typed = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )

        backfill_identity_conversions(self.workspace)

        self.assertEqual(
            live_conversion(
                self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            ).pk,
            typed.pk,
        )

    def test_the_migration_can_be_reversed_without_losing_a_typed_rate(self):
        self.run_backfill()
        typed = record_conversion(
            self.workspace, 'supplier_invoice', self.euro_invoice.pk,
            rate_request(), self.user,
        )
        migration = import_module('bookkeeping.migrations.0004_identity_conversions')

        migration.drop_identity_conversions(django_apps, None)

        self.assertEqual(
            list(CurrencyConversion.objects.values_list('pk', flat=True)), [typed.pk],
        )
