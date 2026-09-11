"""Correcting an expense category, which is the plainest catalog record there is.

It has no code, nothing hangs off it and nothing files it, so every correction
the shared catalog tests already cover holds here unchanged and is not repeated.
What is tested is the one thing this catalog has that no other mergeable one
does: its name is unique inside the workspace.

That is the same pairing the coded settings draw between a warning and a
refusal, reached from the other end. A resembling name is worth saying out
loud and nothing more, because two expenses really can be described alike; an
identical name is not a resemblance at all but exactly the record somebody was
looking for, so it is refused by naming that record and the correction it is
owed.
"""

# Test names state their behavior; repeating it in method docstrings adds noise.
# pylint: disable=missing-function-docstring

from datetime import date
from decimal import Decimal

from purchasing.models import BusinessExpense
from tests.api import RESTContractTestCase
from tests.factories import make_expense_category
from workspaces.models import get_current_workspace

CATEGORIES = '/purchasing/expense-categories/'


class ExpenseCategoryTestCase(RESTContractTestCase):
    """One workspace holding a category and a duplicate of it."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.survivor = make_expense_category(name='Market fees')
        self.duplicate = make_expense_category(name='Market fee')

    def spend(self, category, payee='Saturday market'):
        """Record one confirmed cost against a category."""
        return BusinessExpense.objects.create(
            workspace=self.workspace, category=category, payee=payee,
            incurred_on=date(2026, 8, 22), currency_code='NZD',
            subtotal_ex_tax=Decimal('20.0000'), tax_total=Decimal('3.0000'),
            total_incl_tax=Decimal('23.0000'),
        )


class ExpenseCategoryRetirementTests(ExpenseCategoryTestCase):
    """A retired category leaves the selectors and stays in the ledger."""

    def test_a_retired_category_is_no_longer_offered(self):
        self.client.patch(
            f'{CATEGORIES}{self.survivor.pk}/', {'active': False}, format='json',
        )

        response = self.client.post('/purchasing/expenses/', {
            'category': self.survivor.pk, 'payee': 'Saturday market',
            'incurred_on': '2026-08-22', 'currency_code': 'NZD',
            'subtotal_ex_tax': '20.0000', 'tax_total': '3.0000',
            'total_incl_tax': '23.0000',
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('category', response.data)

    def test_the_collection_still_names_a_retired_category(self):
        """It is what a confirmed expense from last year points at."""
        self.client.patch(
            f'{CATEGORIES}{self.survivor.pk}/', {'active': False}, format='json',
        )

        self.assertIn(self.survivor.name, self.listed_names(CATEGORIES))
        self.assertNotIn(
            self.survivor.name, self.listed_names(CATEGORIES, active='true'),
        )

    def test_a_retired_category_restores(self):
        self.client.patch(
            f'{CATEGORIES}{self.survivor.pk}/', {'active': False}, format='json',
        )

        response = self.client.patch(
            f'{CATEGORIES}{self.survivor.pk}/', {'active': True}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(self.survivor.name, self.listed_names(CATEGORIES, active='true'))

    def test_a_category_cannot_be_deleted(self):
        """Deleting is what retiring exists instead of."""
        response = self.client.delete(f'{CATEGORIES}{self.survivor.pk}/')

        self.assertEqual(response.status_code, 405, response.data)


class ExpenseCategoryMergeTests(ExpenseCategoryTestCase):
    """A merge puts a split expense history back together."""

    def test_a_merge_moves_the_expenses_and_retires_the_duplicate(self):
        expense = self.spend(self.duplicate)

        response = self.client.post(
            f'{CATEGORIES}{self.duplicate.pk}/merge/',
            {'into': self.survivor.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        expense.refresh_from_db()
        self.duplicate.refresh_from_db()
        self.assertEqual(expense.category_id, self.survivor.pk)
        self.assertFalse(self.duplicate.active)
        self.assertEqual(self.duplicate.merged_into_id, self.survivor.pk)

    def test_a_preview_names_what_would_move_and_moves_nothing(self):
        expense = self.spend(self.duplicate)

        response = self.client.get(
            f'{CATEGORIES}{self.duplicate.pk}/merge/', {'into': self.survivor.pk},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['blockers'], [])
        self.assertIn(
            'purchasing.businessexpense.category',
            [reference['relation'] for reference in response.data['references']],
        )
        expense.refresh_from_db()
        self.assertEqual(expense.category_id, self.duplicate.pk)

    def test_the_name_the_duplicate_held_stays_on_it(self):
        """A confirmed expense was filed under it, so it keeps saying so."""
        self.client.post(
            f'{CATEGORIES}{self.duplicate.pk}/merge/',
            {'into': self.survivor.pk}, format='json',
        )

        self.duplicate.refresh_from_db()
        self.assertEqual(self.duplicate.name, 'Market fee')


class ExpenseCategoryNameTests(ExpenseCategoryTestCase):
    """A resemblance is warned about and an identical name is refused."""

    def test_a_resembling_name_already_in_the_catalog_is_reported(self):
        response = self.client.get(f'{CATEGORIES}duplicates/', {'name': 'Market fees'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(
            str(self.survivor),
            [entry['label'] for entry in response.data['candidates']],
        )

    def test_a_resembling_name_is_still_allowed_to_be_saved(self):
        """Two costs really can be described alike; the answer is the operator's."""
        response = self.client.post(
            CATEGORIES, {'name': 'Market feed', 'notes': ''}, format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)

    def test_a_name_already_taken_names_the_category_holding_it(self):
        """It is not a near-duplicate; it is the record being looked for."""
        response = self.client.post(
            CATEGORIES, {'name': 'Market fees', 'notes': ''}, format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(str(self.survivor), response.data['name'][0])
        self.assertIn('Merge into it', response.data['name'][0])

    def test_keeping_a_category_its_own_name_is_not_a_duplicate(self):
        response = self.client.patch(
            f'{CATEGORIES}{self.survivor.pk}/',
            {'name': 'Market fees', 'notes': 'Stall hire.'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)


class ExpenseCategorySearchTests(ExpenseCategoryTestCase):
    """A correction starts by finding the category it is about."""

    def test_a_category_is_found_by_the_start_of_its_name(self):
        self.assertIn(self.survivor.name, self.listed_names(CATEGORIES, search='mark'))

    def test_a_search_that_matches_nothing_narrows_to_nothing(self):
        self.assertEqual(self.listed_names(CATEGORIES, search='freight'), [])
