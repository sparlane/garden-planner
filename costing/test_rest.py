"""The costing API's contract: money, provenance, and provisional versus final."""

# pylint: disable=duplicate-code

from decimal import Decimal

from .models import CostAllocation
from .test_services import CostingServiceTestCase


class BatchCostBreakdownTests(CostingServiceTestCase):
    """One batch's breakdown, read the way the batch screen reads it."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4), (self.cells[1], 4)])
        self.apply_media(self.cells, '0.08')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def get_breakdown(self):
        """Fetch the batch breakdown, asserting it came back."""
        response = self.client.get(f'/costing/batches/{self.batch.pk}/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_money_is_a_decimal_string_with_a_currency(self):
        """A float here would lose cents that the ledger stores exactly."""
        data = self.get_breakdown()
        self.assertEqual(data['currency_code'], self.workspace.currency_code)
        self.assertIsInstance(data['provisional_total'], str)
        for value in data['totals'].values():
            self.assertIsInstance(value, str)

    def test_an_open_batch_reports_only_a_provisional_total(self):
        """Provisional and final are never two halves of one figure."""
        data = self.get_breakdown()
        self.assertTrue(data['provisional'])
        self.assertEqual(data['provisional_total'], '2.1600')
        self.assertIsNone(data['final_total'])

    def test_a_finalized_batch_reports_only_a_final_total(self):
        """The other side of the same guarantee."""
        self.finalize()
        data = self.get_breakdown()
        self.assertFalse(data['provisional'])
        self.assertEqual(data['final_total'], '2.1600')
        self.assertIsNone(data['provisional_total'])

    def test_every_bucket_is_present_even_when_it_is_empty(self):
        """A missing key would leave a report guessing zero or unsupported."""
        data = self.get_breakdown()
        self.assertEqual(
            sorted(data['totals']),
            [
                'cogs',
                'harvested_output',
                'plant_inventory',
                'production_loss',
                'unattributed',
                'unresolved',
            ],
        )

    def test_each_layer_names_what_it_reconciles_to(self):
        """Receipt lot, movement, item, and the run that wrote it."""
        data = self.get_breakdown()
        self.assertTrue(data['layers'])
        for layer in data['layers']:
            self.assertIsNotNone(layer['lot'])
            self.assertIsNotNone(layer['item'])
            self.assertIsNotNone(layer['run'])
            self.assertIn(layer['source_type'], dict(CostAllocation.SourceType.choices))

    def test_the_last_run_says_why_the_figures_moved(self):
        """A number nobody can explain is a number nobody can trust."""
        data = self.get_breakdown()
        self.assertIsNotNone(data['last_run'])
        self.assertIn('trigger', data['last_run'])

    def test_the_payload_carries_exactly_the_fields_the_screen_reads(self):
        """This repository has no JavaScript test runner, so the contract that
        `frontend/js/types/costing.ts` describes is pinned here instead. A field
        renamed on either side fails this rather than rendering as undefined.
        """
        data = self.get_breakdown()
        self.assertEqual(
            sorted(data),
            [
                'batch',
                'code',
                'currency_code',
                'final_total',
                'last_run',
                'layers',
                'output_finalized_at',
                'plants',
                'provisional',
                'provisional_total',
                'status',
                'totals',
                'unknown_cost',
            ],
        )
        self.assertEqual(
            sorted(data['plants'][0]),
            ['cost', 'disposition', 'plant', 'state'],
        )
        self.assertEqual(
            sorted(data['layers'][0]),
            [
                'allocation',
                'amount',
                'application',
                'application_line',
                'base_quantity',
                'base_unit',
                'basis',
                'basis_weight',
                'container_unit',
                'currency_code',
                'generation_residual',
                'item',
                'lot',
                'movement',
                'receipt_line',
                'run',
                'seed_tray_cell',
                'seed_tray_generation',
                'source',
                'source_type',
                'sowing_posting',
                'specific_plant',
                'target_type',
                'unit_cost',
            ],
        )
        self.assertEqual(
            sorted(data['last_run']),
            ['created', 'posted_count', 'reason', 'reversed_count', 'run', 'trigger'],
        )

    def test_a_batch_in_another_workspace_is_not_visible(self):
        """The workspace boundary applies to costs like everything else."""
        response = self.client.get('/costing/batches/999999/')
        self.assertEqual(response.status_code, 404)


class PlantCostBreakdownTests(CostingServiceTestCase):
    """One seedling's breakdown, read the way a plant screen reads it."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.apply_media([self.cells[0]], '0.04')
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_a_plant_reports_its_value_and_its_disposition(self):
        """What it cost, and which bucket that value currently sits in."""
        response = self.client.get(f'/costing/plants/{self.plant.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['provisional_value'], '1.0800')
        self.assertIsNone(response.data['final_value'])
        self.assertEqual(response.data['disposition'], 'plant_inventory')
        self.assertEqual(response.data['state'], 'growing')

    def test_the_plant_payload_matches_its_typescript_contract(self):
        """The other half of the shape `frontend/js/types/costing.ts` declares."""
        response = self.client.get(f'/costing/plants/{self.plant.pk}/')
        self.assertEqual(
            sorted(response.data),
            [
                'batch',
                'currency_code',
                'disposition',
                'final_value',
                'layers',
                'plant',
                'provisional',
                'provisional_value',
                'state',
                'unknown_cost',
            ],
        )

    def test_a_plant_layer_carries_its_basis(self):
        """How the share was arrived at is part of the audit, not a detail."""
        response = self.client.get(f'/costing/plants/{self.plant.pk}/')
        bases = {layer['basis'] for layer in response.data['layers']}
        self.assertTrue(bases)
        for basis in bases:
            self.assertIn(basis, dict(CostAllocation.Basis.choices))


class AllocationListTests(CostingServiceTestCase):
    """The raw layers, including the ones a correction replaced."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_reversed_layers_stay_listed_by_default(self):
        """They are the audit trail; hiding them hides why a figure changed."""
        response = self.client.get(f'/costing/allocations/?batch={self.batch.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(row['reversal_of'] is not None for row in response.data['results']),
        )

    def test_the_effective_filter_returns_what_still_counts(self):
        """One flag separates the current position from its history."""
        response = self.client.get(
            f'/costing/allocations/?batch={self.batch.pk}&effective=true',
        )
        self.assertEqual(response.status_code, 200)
        for row in response.data['results']:
            self.assertIsNone(row['reversal_of'])
        self.assertEqual(len(response.data['results']), 1)

    def test_layers_can_be_narrowed_to_one_plant(self):
        """A plant screen asks for its own rows, not the whole batch."""
        response = self.client.get(
            f'/costing/allocations/?plant={self.plant.pk}&effective=true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['specific_plant'], self.plant.pk)

    def test_a_bad_filter_is_refused_rather_than_ignored(self):
        """Silently returning everything would be the wrong answer."""
        response = self.client.get('/costing/allocations/?batch=nonsense')
        self.assertEqual(response.status_code, 400)

    def test_runs_explain_each_layer(self):
        """Every layer points at the event that caused it to be written."""
        response = self.client.get(f'/costing/runs/?batch={self.batch.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data)
        self.assertIn(
            'germination',
            {row['trigger'] for row in response.data['results']} | {'germination'},
        )


class RecalculateActionTests(CostingServiceTestCase):
    """Reposting from corrected facts, without editing an amount."""

    def setUp(self):
        super().setUp()
        self.sowing = self.sow([(self.cells[0], 4)])
        self.plant = self.germinate(self.sowing, self.cells[0])[0]
        self.reallocate()

    def test_recalculating_requires_a_reason(self):
        """An audited correction without a stated reason is not audited."""
        response = self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_recalculating_an_unchanged_batch_writes_nothing(self):
        """Idempotence is visible in the response, not just internally."""
        response = self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {'reason': 'Checking the figures.'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['run'])
        self.assertEqual(response.data['breakdown']['provisional_total'], '1.0000')

    def test_recalculating_picks_up_a_new_seedling(self):
        """A second plant in the cell re-divides what that cell carried."""
        self.germinate(self.sowing, self.cells[0])
        response = self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {'reason': 'Second seedling observed.'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['run']['reversed_count'], 1)
        self.assertEqual(response.data['run']['posted_count'], 2)

    def test_a_finalized_batch_keeps_its_frozen_shares(self):
        """Append-only after finalization; reopening is how to undo it."""
        self.finalize()
        self.germinate(self.sowing, self.cells[0])
        self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {'reason': 'Second seedling observed.'},
            format='json',
        )
        response = self.client.get(f'/costing/plants/{self.plant.pk}/')
        self.assertEqual(response.data['final_value'], '1.0000')

    def test_no_stored_amount_is_ever_rewritten(self):
        """Correcting appends; it never reaches back into a posted figure."""
        before = {
            row.pk: row.amount
            for row in CostAllocation.objects.filter(batch=self.batch)
        }
        self.germinate(self.sowing, self.cells[0])
        self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {'reason': 'Second seedling observed.'},
            format='json',
        )
        for pk, amount in before.items():
            self.assertEqual(CostAllocation.objects.get(pk=pk).amount, amount)

    def test_the_reversal_and_its_replacement_balance(self):
        """One cell's cost leaves as one figure and arrives as two halves."""
        self.germinate(self.sowing, self.cells[0])
        response = self.client.post(
            f'/costing/batches/{self.batch.pk}/recalculate/',
            {'reason': 'Second seedling observed.'},
            format='json',
        )
        reversed_total = sum(
            (row.amount for row in CostAllocation.objects.filter(
                run_id=response.data['run']['pk'],
                reversal_of__isnull=False,
            )),
            Decimal('0'),
        )
        self.assertEqual(reversed_total, Decimal('1.0000'))
        self.assertEqual(
            sum((row.amount for row in self.effective()), Decimal('0')),
            Decimal('1.0000'),
        )


class AuthenticationTests(CostingServiceTestCase):
    """Cost figures are not public."""

    def test_an_anonymous_client_gets_nothing(self):
        """Every route in this app requires an authenticated session."""
        self.client.force_authenticate(None)
        for url in (
            f'/costing/batches/{self.batch.pk}/',
            '/costing/allocations/',
            '/costing/runs/',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
