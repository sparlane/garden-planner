"""REST contract for drafting, previewing, posting, and reversing."""

# pylint: disable=duplicate-code

from decimal import Decimal

from django.utils import timezone

from inventory.ledger import physical_balance
from inventory.models import InventoryItem, StockMovement
from inventory.units import UnitCode
from tests.api import RESTContractTestCase
from tests.factories import (
    make_inventory_item,
    make_inventory_location,
    make_seed_tray,
    make_seed_tray_cell,
    make_seed_tray_generation,
    make_seed_tray_model,
    make_stock_lot,
)
from workspaces.models import Workspace

from .models import InputApplication, InputApplicationTarget

TargetType = InputApplicationTarget.TargetType
URL = '/applications/input-applications/'


class ApplicationRESTTestCase(RESTContractTestCase):
    """Stock and a tray shared by the REST cases."""

    def setUp(self):
        super().setUp()
        self.location = make_inventory_location()
        self.media = make_inventory_item(
            base_unit=UnitCode.LITRE,
            default_usage_basis=InventoryItem.UsageBasis.CELL_VOLUME,
        )
        self.lot = make_stock_lot(item=self.media, location=self.location, quantity='50')
        model = make_seed_tray_model(cell_size_ml=40, x_cells=24, y_cells=1)
        self.tray = make_seed_tray(model=model)
        self.generation = make_seed_tray_generation(tray=self.tray)
        self.cells = [
            make_seed_tray_cell(tray=self.tray, x_position=index)
            for index in range(24)
        ]

    def payload(self, **overrides):
        """Build a draft payload filling every cell of the tray."""
        line = {
            'item': self.media.pk,
            'lot': self.lot.pk,
            'applied_quantity': '0.960000000',
            'unit_code': UnitCode.LITRE,
            'targets': [
                {'target_type': TargetType.SEED_TRAY_CELL, 'target': cell.pk}
                for cell in self.cells
            ],
        }
        line.update(overrides.pop('line', {}))
        values = {
            'applied_at': timezone.now().isoformat(),
            'source_location': self.location.pk,
            'lines': [line],
        }
        values.update(overrides)
        return values

    def create_draft(self, **overrides):
        """Create one draft and return its response data."""
        response = self.client.post(URL, self.payload(**overrides), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data


class ApplicationContractTests(ApplicationRESTTestCase):
    """The collection follows the shared REST conventions."""

    def test_the_list_route_requires_authentication(self):
        """Anonymous callers cannot read what a workspace applied."""
        self.assert_authentication_required((URL,))

    def test_the_list_route_returns_a_list(self):
        """The collection uses the common unpaginated list contract."""
        self.assert_list_contract((URL,))

    def test_a_draft_reports_its_calculation(self):
        """Creating a draft returns the suggestion it computed."""
        data = self.create_draft()

        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['target_summary'], '24 tray cells')
        line = data['lines'][0]
        self.assertEqual(line['calculated_base_quantity'], '0.960000000')
        self.assertEqual(line['formula_basis_quantity'], '960.000000000')
        self.assertEqual(len(line['targets']), 24)

    def test_a_whole_tray_expands_to_its_cells(self):
        """The shortcut still records exactly which cells were filled."""
        data = self.create_draft(line={'targets': [], 'tray': self.tray.pk})

        self.assertEqual(len(data['lines'][0]['targets']), 24)
        self.assertEqual(data['lines'][0]['calculated_base_quantity'], '0.960000000')

    def test_a_draft_can_be_retrieved(self):
        """A stored draft reads back the same way it was created."""
        created = self.create_draft()
        response = self.client.get(f'{URL}{created["pk"]}/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['pk'], created['pk'])

    def test_a_draft_can_be_deleted(self):
        """An abandoned draft leaves nothing behind."""
        created = self.create_draft()
        response = self.client.delete(f'{URL}{created["pk"]}/')

        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(InputApplication.objects.filter(pk=created['pk']).exists())


class ApplicationPreviewTests(ApplicationRESTTestCase):
    """Preview reports what would happen without doing it."""

    def test_preview_writes_nothing(self):
        """Looking at a document never moves stock."""
        created = self.create_draft()
        before = StockMovement.objects.count()

        response = self.client.get(f'{URL}{created["pk"]}/preview/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_preview_reports_availability_and_the_formula(self):
        """An operator sees the working and the effect before confirming."""
        created = self.create_draft()
        response = self.client.get(f'{URL}{created["pk"]}/preview/')

        line = response.data['lines'][0]
        self.assertEqual(line['available_base_quantity'], '50.000000000')
        self.assertEqual(line['available_after_base_quantity'], '49.040000000')
        self.assertEqual(line['calculated_base_quantity'], '0.960000000')
        self.assertIn('24 cells totalling 960 ml', line['formula'])
        self.assertFalse(line['override_required'])
        self.assertIn('availability_digest', response.data)

    def test_preview_flags_a_required_override(self):
        """The form knows to ask for a reason before the post is attempted."""
        created = self.create_draft(line={'applied_quantity': '1.500000000'})
        response = self.client.get(f'{URL}{created["pk"]}/preview/')

        self.assertTrue(response.data['lines'][0]['override_required'])


class ApplicationPostTests(ApplicationRESTTestCase):
    """Posting decrements the exact lot the document names."""

    def test_posting_consumes_the_confirmed_quantity(self):
        """The confirmed amount is what leaves the shelf."""
        created = self.create_draft()
        response = self.client.post(f'{URL}{created["pk"]}/post/', {}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'posted')
        self.assertIsNotNone(response.data['lines'][0]['consumption_movement'])
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('49.04'))

    def test_posting_with_a_current_digest_succeeds(self):
        """What the operator was shown still holds, so it posts."""
        created = self.create_draft()
        state = self.client.get(f'{URL}{created["pk"]}/preview/').data

        response = self.client.post(
            f'{URL}{created["pk"]}/post/',
            {
                'revision': state['revision'],
                'availability_digest': state['availability_digest'],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_posting_with_a_stale_digest_is_refused(self):
        """Stock spent elsewhere invalidates what the operator confirmed."""
        created = self.create_draft()
        state = self.client.get(f'{URL}{created["pk"]}/preview/').data

        other = self.create_draft(line={'applied_quantity': '1.000000000'})
        self.client.post(f'{URL}{other["pk"]}/post/', {}, format='json')

        response = self.client.post(
            f'{URL}{created["pk"]}/post/',
            {
                'revision': state['revision'],
                'availability_digest': state['availability_digest'],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('availability_digest', response.data)

    def test_editing_a_draft_moves_its_revision(self):
        """An edit in another tab invalidates a preview taken before it."""
        created = self.create_draft()
        state = self.client.get(f'{URL}{created["pk"]}/preview/').data

        edited = self.client.patch(
            f'{URL}{created["pk"]}/',
            self.payload(notes='Reconsidered'),
            format='json',
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data['revision'], state['revision'] + 1)

        response = self.client.post(
            f'{URL}{created["pk"]}/post/',
            {'revision': state['revision']},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('revision', response.data)

    def test_a_material_override_without_a_reason_is_refused(self):
        """The audit wants a departure from the suggestion explained."""
        created = self.create_draft(line={'applied_quantity': '1.500000000'})
        response = self.client.post(f'{URL}{created["pk"]}/post/', {}, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('lines', response.data)

    def test_a_posted_document_cannot_be_edited_or_deleted(self):
        """What moved stock stays exactly as it was recorded."""
        created = self.create_draft()
        self.client.post(f'{URL}{created["pk"]}/post/', {}, format='json')

        patched = self.client.patch(
            f'{URL}{created["pk"]}/',
            self.payload(notes='Too late'),
            format='json',
        )
        self.assertEqual(patched.status_code, 400, patched.data)

        deleted = self.client.delete(f'{URL}{created["pk"]}/')
        self.assertEqual(deleted.status_code, 400, deleted.data)


class ApplicationReverseTests(ApplicationRESTTestCase):
    """Reversal restores stock and keeps the document readable."""

    def posted(self):
        """Create and post one draft."""
        created = self.create_draft()
        self.client.post(f'{URL}{created["pk"]}/post/', {}, format='json')
        return created

    def test_reversal_restores_the_balance(self):
        """Everything the document took goes back."""
        created = self.posted()
        response = self.client.post(
            f'{URL}{created["pk"]}/reverse/',
            {'reason': 'Applied to the wrong tray'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'reversed')
        self.assertEqual(response.data['reverse_reason'], 'Applied to the wrong tray')
        self.assertEqual(physical_balance(self.lot, self.location), Decimal('50'))

    def test_reversal_requires_a_reason(self):
        """A correction that explains nothing is not an audit trail."""
        created = self.posted()
        response = self.client.post(
            f'{URL}{created["pk"]}/reverse/',
            {'reason': ''},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('reason', response.data)

    def test_a_draft_cannot_be_reversed(self):
        """There is nothing to put back until it posted."""
        created = self.create_draft()
        response = self.client.post(
            f'{URL}{created["pk"]}/reverse/',
            {'reason': 'Never mind'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)


class ApplicationFilterTests(ApplicationRESTTestCase):
    """Narrowing the collection to what an operator is looking for."""

    def test_status_and_item_filters(self):
        """Documents narrow by status and by the item they consumed."""
        draft = self.create_draft()
        posted = self.create_draft()
        self.client.post(f'{URL}{posted["pk"]}/post/', {}, format='json')

        drafts = self.client.get(URL, {'status': 'draft'})
        self.assertEqual([row['pk'] for row in drafts.data], [draft['pk']])

        by_item = self.client.get(URL, {'item': self.media.pk})
        self.assertEqual(len(by_item.data), 2)

        other_item = self.client.get(URL, {'item': make_inventory_item().pk})
        self.assertEqual(len(other_item.data), 0)

    def test_an_unparseable_filter_is_refused(self):
        """A malformed filter is a client error, not an empty page."""
        response = self.client.get(URL, {'batch': 'soon'})
        self.assertEqual(response.status_code, 400, response.data)


class ApplicationIsolationTests(ApplicationRESTTestCase):
    """Another workspace's records are neither listed nor accepted."""

    def test_another_workspace_lot_is_refused(self):
        """A document cannot consume stock it does not own."""
        other = Workspace.objects.create(name='Other workspace')
        item = make_inventory_item(workspace=other)
        location = make_inventory_location(workspace=other)
        lot = make_stock_lot(item=item, location=location)

        response = self.client.post(
            URL,
            self.payload(line={'item': item.pk, 'lot': lot.pk}),
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_another_workspace_document_is_not_listed(self):
        """The collection shows only this workspace's applications."""
        other = Workspace.objects.create(name='Other workspace')
        InputApplication.objects.create(
            workspace=other,
            applied_at=timezone.now(),
            source_location=make_inventory_location(workspace=other),
        )
        self.create_draft()

        response = self.client.get(URL)
        self.assertEqual(len(response.data), 1)
