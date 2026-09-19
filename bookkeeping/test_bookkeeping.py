"""Bookkeeping and income-year API contracts."""

# Test names state their behavior and are clearer than repeated method docstrings.
# pylint: disable=missing-function-docstring

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APITestCase

from plantings.lifecycle import EventType, OutcomeRequest, record_lifecycle_event
from tests.factories import make_specific_plant
from workspaces.models import get_current_workspace

from .models import BookkeepingEntry, DepreciationSchedule, IncomeTaxYear, TaxAsset, TaxRetentionRecord
from .services import capture_inventory


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
        retained_asset = TaxAsset.objects.create(
            workspace=self.workspace, code='SPADE-1', name='Spade',
            category='Tools', acquired_on=date(2026, 4, 1),
            cost_incl_tax='100.0000', recoverable_tax='0.0000',
            tax_cost='100.0000', currency_code='NZD', created_by=self.user,
        )
        TaxRetentionRecord.objects.create(
            workspace=self.workspace, source_type='tax_asset',
            source_id=str(retained_asset.pk), income_year_end=date(2027, 3, 31),
            retain_until=date(2034, 3, 31), created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            retained_asset.delete()

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
        retained = TaxRetentionRecord.objects.get(
            source_type='bookkeeping_entry', source_id=str(entry.pk),
        )
        self.assertEqual(retained.retain_until, date(2034, 3, 31))
        held = self.client.post(
            f'/bookkeeping/retention/{retained.pk}/hold/',
            {'active': True, 'reason': 'Audit in progress'}, format='json',
        )
        self.assertEqual(held.status_code, 200, held.data)
        self.assertTrue(held.data['legal_hold'])
        self.assertEqual(len(held.data['hold_events']), 1)

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

    def test_closing_stock_counts_the_plants_still_held(self):
        """Task 126: retained and returned-quarantined stock is valued; gone stock is not.

        The valuation's held set is now derived from `PRESENT_STATES`; this pins
        that doing so changed nothing.
        """
        journeys = {
            'growing': (),
            'available': (EventType.READY,),
            'retained': (EventType.RETAINED,),
            'quarantined': (EventType.READY, EventType.SOLD, EventType.RETURNED_QUARANTINED),
            'sold': (EventType.READY, EventType.SOLD),
            'culled': (EventType.CULLED,),
            'discarded': (EventType.READY, EventType.SOLD, EventType.RETURNED_DISCARDED),
        }
        plants = {}
        for state, event_types in journeys.items():
            plants[state] = make_specific_plant(workspace=self.workspace)
            for event_type in event_types:
                record_lifecycle_event(
                    plants[state], None, OutcomeRequest(event_type, reason='Recorded.'),
                )
        year = IncomeTaxYear.objects.create(
            workspace=self.workspace, basis='accrual',
            year_end=date(timezone.localdate().year + 1, 3, 31),
        )
        capture_inventory(year, self.user)
        captured = set(
            year.stock_lines.filter(source_type='specific_plant')
            .values_list('source_id', flat=True)
        )
        self.assertEqual(captured, {
            str(plants[state].pk)
            for state in ('growing', 'available', 'retained', 'quarantined')
        })
