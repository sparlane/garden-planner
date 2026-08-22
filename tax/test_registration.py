"""Protect the rule that a GST arrangement is appended, never rewritten.

A return already filed has to keep reading the way it was filed. That is not a
convention here, it is what the model refuses to let anybody break: rows are
immutable, a new arrangement cannot be dated before one already recorded, and
a correction is a superseding row that leaves the mistake visible.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspaces.models import Workspace

from .models import GstRegistration
from .periods import registration_history, registration_in_force, taxable_period_for
from .services import record_registration, supersede_registration


REGISTERED = {
    'registered': True,
    'gst_number': '123456785',
    'basis': GstRegistration.Basis.INVOICE,
    'filing_frequency': GstRegistration.Frequency.TWO_MONTHLY,
    'period_anchor_month': 3,
}


class GstRegistrationTestCase(TestCase):
    """Fixture shared by every test that needs an arrangement to exist."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=1)
        self.user = get_user_model().objects.create_user(
            username=f'{self.__class__.__name__}-user',
        )

    def register(self, effective_from=date(2026, 1, 1), **overrides):
        """Record a registered arrangement, defaulting the mandatory fields."""
        values = dict(REGISTERED, effective_from=effective_from)
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)

    def deregister(self, effective_from, **overrides):
        """Record a cessation, which carries no configuration at all."""
        return record_registration(
            self.workspace, self.user,
            registered=False, effective_from=effective_from, **overrides,
        )


class RecordRegistrationTests(GstRegistrationTestCase):
    """The fields a row must and must not carry depend on what it claims."""

    def test_a_registration_is_recorded_with_its_actor(self):
        """Who recorded a tax arrangement is part of the record, not metadata."""
        registration = self.register()
        self.assertEqual(registration.created_by, self.user)
        self.assertEqual(registration.workspace, self.workspace)
        self.assertTrue(registration.registered)

    def test_the_gst_number_is_normalized_on_the_way_in(self):
        """The eight- and nine-digit forms are one number; store one shape."""
        registration = self.register(gst_number='10000009')
        self.assertEqual(registration.gst_number, '010000009')

    def test_an_invalid_gst_number_is_refused(self):
        """A transposed number would file returns against somebody else."""
        with self.assertRaises(ValidationError) as caught:
            self.register(gst_number='123456784')
        self.assertIn('gst_number', caught.exception.message_dict)

    def test_a_registration_needs_its_basis_frequency_and_anchor(self):
        """Without all three, no supply can be assigned to a return period."""
        for field in ('basis', 'filing_frequency', 'period_anchor_month'):
            with self.subTest(field=field):
                blank = '' if field != 'period_anchor_month' else None
                with self.assertRaises(ValidationError) as caught:
                    self.register(**{field: blank})
                self.assertIn(field, caught.exception.message_dict)

    def test_a_deregistration_carries_no_configuration(self):
        """Repeating the old basis would leave it ambiguous whether it still applied."""
        for field, value in (
            ('gst_number', '123456785'),
            ('basis', GstRegistration.Basis.PAYMENTS),
            ('filing_frequency', GstRegistration.Frequency.MONTHLY),
            ('period_anchor_month', 3),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as caught:
                    self.deregister(date(2026, 6, 1), **{field: value})
                self.assertIn(field, caught.exception.message_dict)

    def test_a_taxable_activity_cannot_start_after_its_registration(self):
        """The activity is what the registration is for; it cannot follow it."""
        with self.assertRaises(ValidationError) as caught:
            self.register(taxable_activity_start=date(2026, 6, 1))
        self.assertIn('taxable_activity_start', caught.exception.message_dict)


class AppendOnlyOrderingTests(GstRegistrationTestCase):
    """Backdating over a recorded arrangement is how history gets rewritten."""

    def test_a_first_registration_may_be_backdated_freely(self):
        """Nothing later contradicts it, and a retroactive registration is real."""
        registration = self.register(effective_from=date(2020, 4, 1))
        self.assertEqual(registration.effective_from, date(2020, 4, 1))

    def test_a_new_arrangement_dated_before_the_latest_is_refused(self):
        """Otherwise a filed period would silently change basis after the fact."""
        self.register(effective_from=date(2026, 1, 1))
        with self.assertRaises(ValidationError) as caught:
            self.deregister(date(2025, 12, 1))
        self.assertIn('effective_from', caught.exception.message_dict)

    def test_a_new_arrangement_dated_on_the_latest_is_refused(self):
        """Two live rows on one date make "in force" a coin toss."""
        self.register(effective_from=date(2026, 1, 1))
        with self.assertRaises(ValidationError) as caught:
            self.deregister(date(2026, 1, 1))
        self.assertIn('effective_from', caught.exception.message_dict)

    def test_a_later_arrangement_is_accepted(self):
        """The ordinary case: a basis change from a date after the last one."""
        self.register(effective_from=date(2026, 1, 1))
        changed = self.register(
            effective_from=date(2026, 4, 1), basis=GstRegistration.Basis.PAYMENTS,
        )
        self.assertEqual(len(registration_history(self.workspace)), 2)
        self.assertEqual(changed.basis, GstRegistration.Basis.PAYMENTS)


class ImmutabilityTests(GstRegistrationTestCase):
    """The whole point is that a filed return keeps reading as it was filed."""

    def test_an_arrangement_cannot_be_edited(self):
        """An in-place basis change would restate every period it covered."""
        registration = self.register()
        registration.basis = GstRegistration.Basis.PAYMENTS
        with self.assertRaises(ValidationError):
            registration.save()

    def test_an_arrangement_cannot_be_deleted(self):
        """Deleting it would leave a filed return with no arrangement behind it."""
        registration = self.register()
        with self.assertRaises(ValidationError):
            registration.delete()


class SupersedingTests(GstRegistrationTestCase):
    """A correction leaves the mistake and the correction both readable."""

    def test_a_superseded_row_stops_applying(self):
        """The correction is what applied; the mistake is only what was entered."""
        wrong = self.register(basis=GstRegistration.Basis.PAYMENTS)
        right = supersede_registration(
            wrong, self.user, **dict(REGISTERED, effective_from=date(2026, 1, 1)),
        )
        history = registration_history(self.workspace)
        self.assertEqual([row.pk for row in history], [right.pk])
        in_force = registration_in_force(self.workspace, date(2026, 2, 1))
        self.assertEqual(in_force.basis, GstRegistration.Basis.INVOICE)

    def test_the_superseded_row_is_still_stored(self):
        """It is what a return filed before the correction was filed under."""
        wrong = self.register(basis=GstRegistration.Basis.PAYMENTS)
        supersede_registration(
            wrong, self.user, **dict(REGISTERED, effective_from=date(2026, 1, 1)),
        )
        self.assertTrue(GstRegistration.objects.filter(pk=wrong.pk).exists())

    def test_a_row_cannot_be_superseded_twice(self):
        """Two corrections of one row leave no single answer to what applied."""
        wrong = self.register(basis=GstRegistration.Basis.PAYMENTS)
        supersede_registration(
            wrong, self.user, **dict(REGISTERED, effective_from=date(2026, 1, 1)),
        )
        with self.assertRaises(ValidationError):
            supersede_registration(
                wrong, self.user, **dict(REGISTERED, effective_from=date(2026, 1, 1)),
            )

    def test_a_superseding_row_may_share_the_date_it_corrects(self):
        """A correction is about the same day; refusing that would defeat it."""
        wrong = self.register(period_anchor_month=4)
        right = supersede_registration(
            wrong, self.user, **dict(REGISTERED, effective_from=date(2026, 1, 1)),
        )
        self.assertEqual(right.effective_from, wrong.effective_from)


class RegistrationInForceTests(GstRegistrationTestCase):
    """Nothing invents a period for a date the workspace was not registered."""

    def test_a_date_before_any_registration_has_no_arrangement(self):
        """Commerce predating registration carried no GST obligation."""
        self.register(effective_from=date(2026, 1, 1))
        self.assertIsNone(registration_in_force(self.workspace, date(2025, 12, 31)))
        self.assertIsNone(taxable_period_for(self.workspace, date(2025, 12, 31)))

    def test_a_date_in_a_deregistered_gap_has_no_arrangement(self):
        """A cessation is a fact, so the gap is empty rather than assumed shut."""
        self.register(effective_from=date(2026, 1, 1))
        self.deregister(date(2026, 7, 1))
        self.assertIsNotNone(registration_in_force(self.workspace, date(2026, 6, 30)))
        self.assertIsNone(registration_in_force(self.workspace, date(2026, 7, 1)))
        self.assertIsNone(taxable_period_for(self.workspace, date(2026, 7, 1)))

    def test_registering_again_after_a_gap_applies_from_the_new_date(self):
        """A workspace that re-registers is registered again, not retroactively."""
        self.register(effective_from=date(2026, 1, 1))
        self.deregister(date(2026, 7, 1))
        self.register(effective_from=date(2027, 1, 1))
        self.assertIsNone(registration_in_force(self.workspace, date(2026, 12, 31)))
        self.assertIsNotNone(registration_in_force(self.workspace, date(2027, 1, 1)))


class ClippedPeriodTests(GstRegistrationTestCase):
    """A period covers what a return covers, not always a whole cycle."""

    def test_a_mid_cycle_registration_produces_a_short_first_period(self):
        """Inland Revenue issues a short first period; reporting a full one overstates it."""
        self.register(effective_from=date(2026, 2, 15))
        period = taxable_period_for(self.workspace, date(2026, 3, 1))
        self.assertEqual((period.start, period.end), (date(2026, 2, 15), date(2026, 3, 31)))
        self.assertEqual(
            (period.cycle_start, period.cycle_end), (date(2026, 2, 1), date(2026, 3, 31)),
        )
        self.assertTrue(period.clipped)

    def test_a_mid_cycle_change_closes_the_period_early(self):
        """Two bases inside one period would put one return on both."""
        self.register(effective_from=date(2026, 2, 1))
        self.register(
            effective_from=date(2026, 3, 10), basis=GstRegistration.Basis.PAYMENTS,
        )
        before = taxable_period_for(self.workspace, date(2026, 2, 20))
        self.assertEqual((before.start, before.end), (date(2026, 2, 1), date(2026, 3, 9)))
        self.assertEqual(before.basis, GstRegistration.Basis.INVOICE)
        after = taxable_period_for(self.workspace, date(2026, 3, 20))
        self.assertEqual((after.start, after.end), (date(2026, 3, 10), date(2026, 3, 31)))
        self.assertEqual(after.basis, GstRegistration.Basis.PAYMENTS)

    def test_a_deregistration_closes_the_period_it_falls_in(self):
        """The final return stops on the last day the workspace was registered."""
        self.register(effective_from=date(2026, 2, 1))
        self.deregister(date(2026, 3, 10))
        period = taxable_period_for(self.workspace, date(2026, 2, 20))
        self.assertEqual((period.start, period.end), (date(2026, 2, 1), date(2026, 3, 9)))

    def test_an_unclipped_period_reports_itself_as_whole(self):
        """The flag has to mean something, so the ordinary case must not set it."""
        self.register(effective_from=date(2026, 2, 1))
        period = taxable_period_for(self.workspace, date(2026, 5, 4))
        self.assertEqual((period.start, period.end), (date(2026, 4, 1), date(2026, 5, 31)))
        self.assertFalse(period.clipped)


class DatabaseConstraintTests(GstRegistrationTestCase):
    """The shape rules survive a writer that bypasses full_clean.

    `bulk_create` skips `save`, and with it every validation this app writes in
    Python. What is left is the constraint, which is the reason the same rule
    is stated twice.
    """

    def test_a_registered_row_without_a_basis_is_refused_by_the_database(self):
        """Validation can be bypassed; a check constraint cannot."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            GstRegistration.objects.bulk_create([GstRegistration(
                workspace=self.workspace,
                registered=True,
                effective_from=date(2026, 1, 1),
                gst_number='123456785',
                basis='',
                filing_frequency=GstRegistration.Frequency.MONTHLY,
                period_anchor_month=1,
            )])

    def test_a_deregistered_row_carrying_a_basis_is_refused_by_the_database(self):
        """The same rule from the other side, at the same depth."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            GstRegistration.objects.bulk_create([GstRegistration(
                workspace=self.workspace,
                registered=False,
                effective_from=date(2026, 1, 1),
                basis=GstRegistration.Basis.INVOICE,
            )])

    def test_an_out_of_range_anchor_month_is_refused_by_the_database(self):
        """A thirteenth month would index a period that does not exist."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            GstRegistration.objects.bulk_create([GstRegistration(
                workspace=self.workspace,
                registered=True,
                effective_from=date(2026, 1, 1),
                gst_number='123456785',
                basis=GstRegistration.Basis.INVOICE,
                filing_frequency=GstRegistration.Frequency.TWO_MONTHLY,
                period_anchor_month=13,
            )])
