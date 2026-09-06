"""Merging a duplicate catalog record into the one it duplicates."""

from datetime import date
from decimal import Decimal

from purchasing.services import confirm_invoice, create_invoice
from supplies.defaults import ensure_default_supplier
from tests.api import RESTContractTestCase
from tests.factories import (
    make_plant,
    make_plant_family,
    make_plant_variety,
    make_planning_assumption,
    make_production_batch,
    make_seeds,
    make_supplier,
)
from workspaces.models import get_current_workspace


class CatalogMergeTests(RESTContractTestCase):
    """A merge moves every reference and changes what none of them mean."""

    def setUp(self):
        super().setUp()
        self.family = make_plant_family(name='Nightshades')
        self.plant = make_plant(family=self.family, name='Tomato')
        self.survivor = make_plant_variety(plant=self.plant, name='Roma')
        self.duplicate = make_plant_variety(plant=self.plant, name='Romar')

    def preview(self, source, into):
        """Ask what merging one variety into another would move."""
        return self.client.get(
            f'/plants/variety/{source.pk}/merge/', {'into': into.pk},
        )

    def merge(self, source, into):
        """Ask for the merge itself."""
        return self.client.post(
            f'/plants/variety/{source.pk}/merge/', {'into': into.pk}, format='json',
        )

    def test_a_preview_names_what_would_move_and_moves_nothing(self):
        """Nobody should have to merge a record to find out what is on it."""
        seeds = make_seeds(plant_variety=self.duplicate)

        response = self.preview(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['blockers'], [])
        self.assertEqual(response.data['references'], [{
            'relation': 'seeds.seeds.plant_variety',
            'noun': 'seeds',
            'count': 1,
            'examples': [str(seeds)],
        }])
        seeds.refresh_from_db()
        self.assertEqual(seeds.plant_variety_id, self.duplicate.pk)

    def test_a_merge_moves_every_reference_onto_the_survivor(self):
        """The split history is the reason to merge, so all of it moves."""
        seeds = make_seeds(plant_variety=self.duplicate)
        batch = make_production_batch(variety=self.duplicate)

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 200, response.data)
        seeds.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(seeds.plant_variety_id, self.survivor.pk)
        self.assertEqual(batch.variety_id, self.survivor.pk)

    def test_the_duplicate_is_retired_pointing_at_where_it_went(self):
        """It keeps the identity anything already written names."""
        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['source']['active'])
        self.assertEqual(response.data['source']['merged_into'], self.survivor.pk)
        detail = self.client.get(f'/plants/variety/{self.duplicate.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['name'], 'Romar')

    def test_a_merged_duplicate_is_no_longer_offered_as_a_choice(self):
        """Retirement is what takes it out of the selectors, and it applies."""
        self.merge(self.duplicate, self.survivor)

        offerable = self.client.get('/plants/variety/', {'active': 'true'}).data

        self.assertEqual(
            {row['pk'] for row in offerable['results']}, {self.survivor.pk},
        )

    def test_a_record_cannot_be_merged_into_itself(self):
        """The duplicate would end up retired with nothing left to name it."""
        response = self.merge(self.survivor, self.survivor)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['into'], ['A record cannot be merged into itself.'],
        )

    def test_merging_across_plants_is_refused_as_a_reclassification(self):
        """A merge moves references; it never changes what one of them means."""
        other = make_plant_variety(
            plant=make_plant(family=self.family, name='Capsicum'), name='Romar',
        )

        response = self.merge(self.duplicate, other)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['into'], ['Move Romar under Capsicum first.'])

    def test_a_retired_survivor_is_refused(self):
        """Merging into it would move the history out of use as well."""
        self.client.patch(
            f'/plants/variety/{self.survivor.pk}/', {'active': False}, format='json',
        )

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['into'], ['Restore Roma first.'])

    def test_a_merge_that_would_drop_a_reference_is_refused(self):
        """Two assumptions from one date cannot both survive the move."""
        make_planning_assumption(
            variety=self.survivor, effective_from=date(2026, 7, 1),
        )
        make_planning_assumption(
            variety=self.duplicate, effective_from=date(2026, 7, 1),
        )

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 400)
        self.assertIn('would collide with', response.data['into'][0])

    def test_a_refused_merge_leaves_the_duplicate_where_it_was(self):
        """A merge is all of the references or none of them."""
        make_planning_assumption(
            variety=self.survivor, effective_from=date(2026, 7, 1),
        )
        make_planning_assumption(
            variety=self.duplicate, effective_from=date(2026, 7, 1),
        )
        seeds = make_seeds(plant_variety=self.duplicate)

        self.merge(self.duplicate, self.survivor)

        seeds.refresh_from_db()
        self.duplicate.refresh_from_db()
        self.assertEqual(seeds.plant_variety_id, self.duplicate.pk)
        self.assertTrue(self.duplicate.active)

    def test_a_second_merge_moves_the_first_duplicate_along_with_it(self):
        """The trail stays one hop, so nothing has to follow a chain."""
        third = make_plant_variety(plant=self.plant, name='Romaa')
        self.merge(third, self.duplicate)

        self.merge(self.duplicate, self.survivor)

        third.refresh_from_db()
        self.assertEqual(third.merged_into_id, self.survivor.pk)

    def test_a_duplicate_that_already_merged_cannot_merge_again(self):
        """It has nothing left to move, and the trail says where it went."""
        self.merge(self.duplicate, self.survivor)
        third = make_plant_variety(plant=self.plant, name='Romaa')

        response = self.merge(self.duplicate, third)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['into'], ['Romar was already merged into Roma.'],
        )

    def test_merging_into_a_duplicate_points_at_the_survivor_instead(self):
        """The refusal names where the references actually ended up."""
        self.merge(self.duplicate, self.survivor)
        third = make_plant_variety(plant=self.plant, name='Romaa')

        response = self.merge(third, self.duplicate)

        self.assertEqual(response.status_code, 400)
        self.assertIn('Merge into Roma instead.', response.data['into'])

    def test_a_plant_merge_carries_its_varieties_across(self):
        """Merging above the leaves refiles everything under them."""
        duplicate_plant = make_plant(family=self.family, name='Tomatoe')
        variety = make_plant_variety(plant=duplicate_plant, name='Beefsteak')

        response = self.client.post(
            f'/plants/plant/{duplicate_plant.pk}/merge/',
            {'into': self.plant.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        variety.refresh_from_db()
        self.assertEqual(variety.plant_id, self.plant.pk)

    def test_a_merge_needs_a_record_to_merge_into(self):
        """An unnamed survivor is a mistake rather than a no-op."""
        response = self.client.post(
            f'/plants/variety/{self.duplicate.pk}/merge/', {}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['into'], ['Name the record to merge into.'],
        )

    def test_a_seed_catalog_entry_has_no_merge_route(self):
        """It owns a stock identity, so its stock would have to move too."""
        seeds = make_seeds(plant_variety=self.survivor)

        response = self.client.post(
            f'/seeds/seeds/{seeds.pk}/merge/', {'into': seeds.pk}, format='json',
        )

        self.assertEqual(response.status_code, 404)


class SupplierMergeTests(RESTContractTestCase):
    """A posted document keeps what it says about a merged supplier."""

    def setUp(self):
        super().setUp()
        self.workspace = get_current_workspace()
        self.survivor = make_supplier(name='Kings Seeds', address='Katikati')
        self.duplicate = make_supplier(name='Kings seeds', address='Katikati')

    def make_invoice(self, supplier, reference):
        """Confirm one payable against a supplier, snapshotting its identity."""
        invoice = create_invoice(
            self.workspace,
            self.user,
            {
                'supplier': supplier,
                'external_reference': reference,
                'invoice_date': date(2026, 8, 1),
                'currency_code': 'NZD',
            },
            [{
                'description': 'Seed order',
                'subtotal_ex_tax': Decimal('10'),
                'tax_rate': Decimal('15'),
                'tax_total': Decimal('1.5'),
                'total_incl_tax': Decimal('11.5'),
            }],
        )
        return confirm_invoice(invoice, self.user)

    def merge(self, source, into):
        """Ask for one supplier to be merged into another."""
        return self.client.post(
            f'/supplies/supplier/{source.pk}/merge/', {'into': into.pk}, format='json',
        )

    def test_a_posted_document_keeps_the_identity_it_was_issued_with(self):
        """Merging corrects the catalog, not what a document already says."""
        invoice = self.make_invoice(self.duplicate, 'SUP-100')

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 200, response.data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.supplier_id, self.survivor.pk)
        self.assertEqual(invoice.supplier_name_snapshot, 'Kings seeds')

    def test_two_invoices_on_one_reference_refuse_the_merge(self):
        """Moving a reference must never be allowed to drop one."""
        self.make_invoice(self.survivor, 'SUP-100')
        self.make_invoice(self.duplicate, 'SUP-100')

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 400)
        self.assertIn('would collide with', response.data['into'][0])

    def test_a_seed_catalog_entry_follows_its_supplier(self):
        """The catalog under the duplicate is the point of the merge."""
        seeds = make_seeds(supplier=self.duplicate)

        response = self.merge(self.duplicate, self.survivor)

        self.assertEqual(response.status_code, 200, response.data)
        seeds.refresh_from_db()
        self.assertEqual(seeds.supplier_id, self.survivor.pk)

    def test_the_stand_in_supplier_cannot_be_merged_away(self):
        """An unnamed Basic Garden purchase still has to land somewhere."""
        supplier = ensure_default_supplier(self.workspace)

        response = self.merge(supplier, self.survivor)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['into'], ['The stand-in supplier cannot be merged away.'],
        )
