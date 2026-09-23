"""Explicit non-labor expenses reconcile to plants without duplicating stock costs."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError

from plantings.cohorts import observe_cohort
from purchasing.models import BusinessExpense, ExpenseCategory
from purchasing.services import cancel_expense, confirm_expense
from workspaces.models import Workspace

from .models import CostAllocation
from .test_services import CohortStockTestCase, CostingServiceTestCase


class BatchExpenseTests(CostingServiceTestCase):
    """Confirmation, redistribution and cancellation preserve the audit ledger."""

    def expense(self, **overrides):
        """Create a tax-recoverable, deliberately opted-in batch expense."""
        fields = {
            'workspace': self.workspace,
            'category': ExpenseCategory.objects.create(workspace=self.workspace, name=str(uuid4())),
            'payee': 'Greenhouse power', 'incurred_on': date(2026, 9, 1),
            'currency_code': 'NZD', 'subtotal_ex_tax': Decimal('10'),
            'tax_total': Decimal('1.5'), 'total_incl_tax': Decimal('11.5'),
            'claim_input_tax': True, 'claimable_percentage': Decimal('100'),
            'production_batch': self.batch, 'batch_cost_treatment': 'non_labor',
        }
        fields.update(overrides)
        return BusinessExpense.objects.create(**fields)

    def expense_layers(self):
        """Read only effective expense layers, independently of seed costs."""
        return [row for row in self.effective() if row.business_expense_id]

    def test_direct_plants_share_after_tax_amount_and_cancel_reverses(self):
        """An identified plant receives its share even without an anonymous cohort."""
        sowing = self.sow([(self.cells[0], 2)])
        self.germinate(sowing, self.cells[0], count=2)
        expense = confirm_expense(self.expense(), self.user)
        rows = self.expense_layers()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.target_type == 'specific_plant' for row in rows))
        self.assertEqual([row.amount for row in rows], [Decimal('5'), Decimal('5')])
        self.assertIsNone(self.reallocate())
        self.finalize()
        cancel_expense(expense, self.user)
        self.assertEqual(self.expense_layers(), [])
        self.assertEqual(CostAllocation.objects.filter(reversal_of__in=rows).count(), 2)

    def test_link_alone_does_not_charge_and_batch_is_required(self):
        """Existing linked expenses remain excluded unless deliberately selected."""
        confirm_expense(self.expense(batch_cost_treatment='excluded'), self.user)
        self.assertEqual(self.expense_layers(), [])
        with self.assertRaises(ValidationError):
            self.expense(production_batch=None)
        with self.assertRaises(ValidationError):
            self.expense(batch_cost_treatment='labor')

    def test_pool_moves_to_observed_plants_and_late_final_cost_is_allocated(self):
        """An expense can precede germination or arrive after output finalization."""
        confirm_expense(self.expense(currency_code='USD'), self.user)
        self.assertEqual(self.expense_layers()[0].target_type, 'batch_pool')
        sowing = self.sow([(self.cells[0], 1)])
        self.germinate(sowing, self.cells[0])
        self.reallocate()
        self.assertEqual(self.expense_layers()[0].target_type, 'specific_plant')
        self.assertEqual(self.expense_layers()[0].currency_code, 'USD')
        self.finalize()
        confirm_expense(self.expense(), self.user)
        self.assertEqual(len(self.expense_layers()), 2)
        self.assertTrue(all(row.target_type == 'specific_plant' for row in self.expense_layers()))
        self.assert_sources_reconcile()

    def test_cohorts_and_direct_plants_share_one_expense(self):
        """A direct identity and anonymous units each receive a single unit share."""
        sowing = self.sow([(self.cells[0], 1), (self.cells[1], 3)])
        self.germinate(sowing, self.cells[0])
        observe_cohort(self.workspace, self.user, batch=self.batch, source_sowing=sowing,
                       quantity=3, idempotency_key=uuid4())
        confirm_expense(self.expense(), self.user)
        amounts = {row.target_type: row.amount for row in self.expense_layers()}
        self.assertEqual(amounts, {'specific_plant': Decimal('2.5'), 'plant_cohort': Decimal('7.5')})
        self.assert_sources_reconcile()

    def test_api_confirms_cancels_and_scopes_expense(self):
        """The public workflow preserves immutable inputs and workspace isolation."""
        draft = self.expense(batch_cost_treatment='excluded')
        url = f'/purchasing/expenses/{draft.pk}/'
        updated = self.client.patch(url, {'batch_cost_treatment': 'non_labor'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        confirmed = self.client.post(f'{url}confirm/', {}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data['batch_cost_treatment'], 'non_labor')
        self.assertEqual(len(self.expense_layers()), 1)
        immutable = self.client.patch(url, {'batch_cost_treatment': 'excluded'}, format='json')
        self.assertEqual(immutable.status_code, 400, immutable.data)
        cancelled = self.client.post(f'{url}cancel/', {}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], 'cancelled')
        self.assertEqual(self.expense_layers(), [])
        other = Workspace.objects.create(name='Other expenses')
        category = ExpenseCategory.objects.create(workspace=other, name='Power')
        foreign = self.expense(workspace=other, category=category, production_batch=None,
                               batch_cost_treatment='excluded')
        response = self.client.post(f'/purchasing/expenses/{foreign.pk}/cancel/', {}, format='json')
        self.assertEqual(response.status_code, 404, response.data)


class CohortExpenseTests(CohortStockTestCase):
    """Sold and lost anonymous units retain their expense shares."""

    expense = BatchExpenseTests.expense
    expense_layers = BatchExpenseTests.expense_layers

    def test_sale_loss_and_promotion_preserve_per_output_expense(self):
        """Four outputs keep 2.5 each through sale, loss and identification."""
        confirm_expense(self.expense(), self.user)
        self.finalize()
        self.sell()
        self.lose()
        self.promote_one()
        self.assertEqual(
            {row.target_type: row.amount for row in self.expense_layers()},
            {target: Decimal('2.5') for target in (
                'specific_plant', 'plant_cohort', 'cohort_sale', 'cohort_loss',
            )},
        )
        self.assert_sources_reconcile()
