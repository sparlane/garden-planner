"""Bookkeeping and income-year API contracts."""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from workspaces.models import get_current_workspace

from .models import BookkeepingEntry, DepreciationSchedule, IncomeTaxYear, TaxAsset


class BookkeepingTests(APITestCase):
    """Tax working papers retain source identities and immutable arithmetic."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.currency_code = 'NZD'
        self.workspace.timezone = 'Pacific/Auckland'
        self.workspace.mode = 'nursery'
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='bookkeeper')
        self.client.force_authenticate(self.user)

    def test_entry_reversal_is_append_only(self):
        created = self.client.post('/bookkeeping/entries/', {
            'kind': 'other_income', 'occurred_on': '2026-06-01',
            'description': 'Workshop', 'amount_ex_tax': '100.0000',
            'tax_amount': '15.0000', 'total_incl_tax': '115.0000',
            'tax_treatment': 'standard', 'currency_code': 'NZD',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        reversed_response = self.client.post(
            f"/bookkeeping/entries/{created.data['id']}/reverse/",
            {'reason': 'Entered twice'}, format='json',
        )
        self.assertEqual(reversed_response.status_code, 201, reversed_response.data)
        self.assertEqual(reversed_response.data['reversal_of'], created.data['id'])
        with self.assertRaises(ValidationError):
            BookkeepingEntry.objects.get(pk=created.data['id']).delete()

    def test_asset_schedule_reconciles_entered_values(self):
        asset = TaxAsset.objects.create(
            workspace=self.workspace, code='TILLER-1', name='Tiller',
            category='Machinery', acquired_on=date(2026, 4, 1),
            cost_incl_tax='1150.0000', recoverable_tax='150.0000',
            tax_cost='1000.0000', currency_code='NZD', created_by=self.user,
        )
        schedule = DepreciationSchedule.objects.create(
            workspace=self.workspace, asset=asset,
            income_year_end=date(2027, 3, 31), method='dv',
            rate_percent='20.0000', business_use_percent='100.0000',
            months_used=12, opening_tax_value='1000.0000',
            depreciation_claimed='200.0000', disposal_adjustment='0.0000',
            closing_tax_value='800.0000', created_by=self.user,
        )
        self.assertEqual(str(schedule.closing_tax_value), '800.0000')

    def test_income_year_can_capture_and_finalize_confirmed_zero_opening(self):
        entry = BookkeepingEntry.objects.create(
            workspace=self.workspace, kind='other_income',
            occurred_on=date(2026, 6, 1), description='Workshop',
            amount_ex_tax='100.0000', tax_amount='0.0000',
            total_incl_tax='100.0000', currency_code='NZD', created_by=self.user,
        )
        created = self.client.post('/bookkeeping/income-years/', {
            'year_end': '2027-03-31', 'basis': 'accrual',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        captured = self.client.post(
            f"/bookkeeping/income-years/{created.data['id']}/capture/", {}, format='json',
        )
        self.assertEqual(captured.status_code, 200, captured.data)
        finalized = self.client.post(
            f"/bookkeeping/income-years/{created.data['id']}/finalize/",
            {'confirm_zero_opening': True}, format='json',
        )
        self.assertEqual(finalized.status_code, 200, finalized.data)
        year = IncomeTaxYear.objects.get(pk=created.data['id'])
        self.assertEqual(year.status, IncomeTaxYear.Status.FINALIZED)
        self.assertEqual(year.frozen_report['totals']['other_income'], '100.0000')
        self.assertEqual(year.frozen_report['rows'][0]['source_id'], entry.pk)
        self.assertEqual(year.retain_until, date(2034, 3, 31))

    def test_manual_stock_requires_evidence_and_market_value_below_cost(self):
        year = IncomeTaxYear.objects.create(
            workspace=self.workspace, year_end=date(2027, 3, 31), basis='accrual',
        )
        response = self.client.post(f'/bookkeeping/income-years/{year.pk}/stock-lines/', {
            'category': 'other', 'description': 'Packed produce',
            'source_type': 'manual', 'source_id': 'PACKED-1',
            'original_cost': '50.0000', 'method': 'market_selling',
            'value': '60.0000', 'currency_code': 'NZD',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_non_march_balance_date_is_rejected(self):
        response = self.client.post('/bookkeeping/income-years/', {
            'year_end': '2027-06-30', 'basis': 'cash',
        }, format='json')
        self.assertEqual(response.status_code, 400)
