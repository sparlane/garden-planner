"""The workspace's tax rate fills in every field that requires one (task 160).

A workspace states its rate once. The five lines an operator enters a rate on --
the sales order line, the purchase order line, the supplier invoice line, the
stock receipt line and the seed packet receipt that writes one -- take it when
the request names none, and the requisition-to-order action takes it too.

The three things that make that safe are pinned beside it: a rate stated in the
request always wins, a line classified away from the standard rate stays at
zero, and a workspace that changes its rate afterwards changes nothing already
recorded.
"""

# pylint: disable=duplicate-code

from decimal import Decimal

from inventory.models import StockReceiptLine
from inventory.units import UnitCode
from purchasing.models import PurchaseOrderLine, SupplierInvoiceLine
from sales.models import SalesOrder, SalesOrderLine
from tests.api import RESTContractTestCase
from tests.factories import (
    make_inventory_item,
    make_location,
    make_nursery_workspace,
    make_plant_variety,
    make_supplier,
)

from .models import Workspace, get_current_workspace
from .tax import ZERO, unentered_tax_rate

#: What this workspace charges, and what every omitted rate below comes out as.
RATE = '15.0000'

#: What it is changed to once every document has been stored.
LATER_RATE = Decimal('25')

#: An unrelated edit, for each document that takes a partial write. Nothing
#: here names a rate or a treatment: the point is that the stored rate is not
#: revisited by a write that was about something else.
PARTIAL_EDIT = {
    'stock receipt line': {'supplier_reference': 'DEL-160-CHECKED'},
    'sales order line': {'unit_price': '12.0000'},
    'seed packet receipt': {'supplier_lot_reference': 'LOT-160-B'},
}

#: The two documents whose update replaces their lines wholesale, and which a
#: client edits by sending the whole body back through PUT.
REPLACED_WHOLE = ('purchase order line', 'supplier invoice line')


def read_rate(data, path):
    """Walk a created document's payload to the rate it stored."""
    for step in path:
        data = data[step]
    return data


class TaxRateRuleTests(RESTContractTestCase):
    """The rule on its own, before any document is in front of it."""

    def test_an_unstated_treatment_takes_the_workspaces_rate(self):
        """A line nobody has classified away from the standard rate."""
        workspace = make_nursery_workspace(default_tax_rate=Decimal('15'))
        self.assertEqual(unentered_tax_rate(workspace), Decimal('15'))
        self.assertEqual(unentered_tax_rate(workspace, ''), Decimal('15'))
        self.assertEqual(unentered_tax_rate(workspace, 'standard'), Decimal('15'))

    def test_every_other_treatment_is_a_rate_of_zero(self):
        """Not a preference: the database says so on a sales line."""
        workspace = make_nursery_workspace(default_tax_rate=Decimal('15'))
        for treatment in (
            'zero_rated', 'exempt', 'out_of_scope', 'unclassified', 'unknown',
        ):
            with self.subTest(treatment=treatment):
                self.assertEqual(unentered_tax_rate(workspace, treatment), ZERO)

    def test_a_workspace_that_charges_nothing_fills_in_nothing(self):
        """Zero is a legitimate default, not a placeholder for an unset one."""
        workspace = make_nursery_workspace()
        self.assertEqual(workspace.default_tax_rate, Decimal('0'))
        self.assertEqual(unentered_tax_rate(workspace), Decimal('0'))
        self.assertEqual(Workspace(name='Second bench').default_tax_rate, Decimal('0'))


class TaxRateInputTestCase(RESTContractTestCase):
    """One nursery charging 15%, and the lines a rate is entered on."""

    def setUp(self):
        super().setUp()
        self.workspace = make_nursery_workspace(default_tax_rate=Decimal('15'))
        self.supplier = make_supplier(workspace=self.workspace)
        self.location = make_location()
        self.item = make_inventory_item(base_unit=UnitCode.LITRE)
        self.variety = make_plant_variety()
        # Through the endpoint rather than the factory: a seed product only has
        # an inventory item to receive packets into once it has been created.
        seeds = self.client.post('/seeds/seeds/', {
            'supplier': self.supplier.pk,
            'plant_variety': self.variety.pk,
            'supplier_code': 'BEET-160',
            'base_unit': 'seed',
        }, format='json')
        self.assertEqual(seeds.status_code, 201, seeds.data)
        self.seeds = seeds.data
        order = self.client.post(
            '/sales/orders/',
            {'status': SalesOrder.Status.DRAFT, 'notes': 'Counter order'},
            format='json',
        )
        self.assertEqual(order.status_code, 201, order.data)
        self.order = order.data

    def receipt_payload(self, **line_overrides):
        """Receive two litres of media, naming no rate."""
        line = {
            'item': self.item.pk,
            'quantity': '2.000000000',
            'unit_code': UnitCode.LITRE,
            'line_cost_ex_tax': '10.0000',
            'supplier_cost_incl_tax': '11.5000',
            'tax_treatment': 'standard',
            'input_tax_source': 'supplier',
            'input_tax_amount': '1.5000',
            'claim_input_tax': False,
            'claimable_percentage': '0.0000',
            'destination': self.location.pk,
        }
        return {
            'supplier': self.supplier.pk,
            'received_date': '2026-08-01',
            'supplier_reference': 'DEL-160',
            'lines': [{**line, **line_overrides}],
        }

    def order_payload(self, **line_overrides):
        """Order the same media instead of receiving it."""
        line = {
            'item': self.item.pk,
            'description': 'Growing media',
            'quantity': '100.000000000',
            'unit_code': UnitCode.LITRE,
            'unit_price_ex_tax': '0.1000',
            'freight_ex_tax': '2.0000',
        }
        return {
            'order_number': 'PO-160',
            'supplier': self.supplier.pk,
            'ordered_on': '2026-08-15',
            'currency_code': 'NZD',
            'lines': [{**line, **line_overrides}],
        }

    def invoice_payload(self, **line_overrides):
        """Take the supplier's bill for it."""
        line = {
            'description': 'Growing media',
            'is_freight': False,
            'subtotal_ex_tax': '10.0000',
            'tax_treatment': 'standard',
            'tax_total': '1.5000',
            'total_incl_tax': '11.5000',
        }
        return {
            'supplier': self.supplier.pk,
            'external_reference': 'INV-160',
            'invoice_date': '2026-08-21',
            'due_date': '2026-09-20',
            'currency_code': 'NZD',
            'lines': [{**line, **line_overrides}],
        }

    def sales_line_payload(self, **overrides):
        """Sell one seedling off the counter order."""
        return {
            'order': self.order['pk'],
            'line_type': 'seedling',
            'variety': self.variety.pk,
            'description': self.variety.name,
            'quantity': 1,
            'unit_price': '11.5000',
            'discount_type': 'none',
            'discount_value': '0',
            **overrides,
        }

    def packet_payload(self, **overrides):
        """Receive one seed packet, which writes a receipt line of its own."""
        return {
            'seeds': self.seeds['pk'],
            'quantity_certainty': 'exact',
            'quantity': '50',
            'line_price': '6.0000',
            'received_date': '2026-03-10',
            'supplier_cost_incl_tax': '6.9000',
            'tax_treatment': 'standard',
            'input_tax_source': 'supplier',
            'input_tax_amount': '0.9000',
            'claim_input_tax': False,
            'claimable_percentage': '0',
            **overrides,
        }

    def entry_points(self, **line_overrides):
        """Return one create request per line a rate is entered on.

        Each row is the line's name, where it is posted, the payload, and the
        path through the created document to the rate it stored.
        """
        return (
            (
                'stock receipt line', '/inventory/receipts/',
                self.receipt_payload(**line_overrides), ('lines', 0, 'tax_rate'),
            ),
            (
                'purchase order line', '/purchasing/orders/',
                self.order_payload(**line_overrides), ('lines', 0, 'tax_rate'),
            ),
            (
                'supplier invoice line', '/purchasing/invoices/',
                self.invoice_payload(**line_overrides), ('lines', 0, 'tax_rate'),
            ),
            (
                'sales order line', '/sales/order-lines/',
                self.sales_line_payload(**line_overrides), ('tax_rate',),
            ),
            (
                'seed packet receipt', '/seeds/packet-receipts/',
                self.packet_payload(**line_overrides), ('tax_rate',),
            ),
        )

    def review_a_requisition(self):
        """Raise and review one need, so it can be converted into an order."""
        requisition = self.client.post('/purchasing/requisitions/', {
            'item': self.item.pk,
            'required_on': '2026-09-10',
            'quantity': '100.000000000',
            'unit_code': UnitCode.LITRE,
            'preferred_supplier': self.supplier.pk,
            'estimated_total_incl_tax': '11.5000',
            'notes': 'The benches are out of media.',
        }, format='json')
        self.assertEqual(requisition.status_code, 201, requisition.data)
        reviewed = self.client.post(
            f"/purchasing/requisitions/{requisition.data['pk']}/review/", {},
            format='json',
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        return requisition.data['pk']

    def convert(self, requisition_pk, **overrides):
        """Convert a reviewed need into an order on the terms given."""
        return self.client.post(
            f'/purchasing/requisitions/{requisition_pk}/order/', {
                'order_number': 'PO-REQ-160',
                'supplier': self.supplier.pk,
                'ordered_on': '2026-08-15',
                'currency_code': 'NZD',
                'unit_price_ex_tax': '0.1000',
                **overrides,
            }, format='json',
        )

    def create(self, url, payload):
        """Post one document and return what was created."""
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data


class OmittedRateTests(TaxRateInputTestCase):
    """Verification 1: nobody types a rate the workspace already states."""

    def test_every_line_takes_the_workspaces_rate_when_none_is_named(self):
        """Five lines in four apps, one answer."""
        for name, url, payload, path in self.entry_points():
            with self.subTest(line=name):
                created = self.create(url, payload)
                self.assertEqual(read_rate(created, path), RATE)

    def test_the_stored_line_is_standard_rated_to_match(self):
        """A filled rate and the treatment beside it cannot disagree."""
        self.create('/sales/order-lines/', self.sales_line_payload())
        line = SalesOrderLine.objects.get()
        self.assertEqual(line.tax_rate, Decimal('15'))
        self.assertEqual(line.tax_treatment, SalesOrderLine.TaxTreatment.STANDARD)

    def test_converting_a_requisition_takes_it_too(self):
        """The one rate entered through an action rather than a document."""
        response = self.convert(self.review_a_requisition())

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['lines'][0]['tax_rate'], RATE)

    def test_an_invoice_line_charging_no_tax_is_not_given_a_rate(self):
        """A bill that charged nil tax is not handed a rate beside the nil.

        An invoice line states its tax as an amount off the supplier's
        document, and `SupplierInvoiceLine` checks only that the three amounts
        reconcile -- it has no treatment-and-rate check of the kind a sales or
        receipt line carries. So nothing downstream would refuse a filled rate
        standing beside a zero `tax_total`, and it would reach the GST entry
        and the GST detail export as a standard-rated line charging nothing.
        """
        created = self.create('/purchasing/invoices/', self.invoice_payload(
            tax_total='0.0000', total_incl_tax='10.0000',
        ))

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')
        self.assertEqual(created['lines'][0]['tax_treatment'], 'standard')
        self.assertEqual(SupplierInvoiceLine.objects.get().tax_rate, ZERO)

    def test_an_invoice_line_that_charged_tax_still_takes_the_rate(self):
        """The nil-tax rule is about the amount, not about invoices at large."""
        created = self.create('/purchasing/invoices/', self.invoice_payload())

        self.assertEqual(created['lines'][0]['tax_rate'], RATE)

    def test_a_workspace_charging_nothing_still_records_a_line(self):
        """A nursery that is not registered for GST enters no rate either."""
        self.workspace.default_tax_rate = Decimal('0')
        self.workspace.save()

        created = self.create(
            '/purchasing/orders/', self.order_payload(),
        )

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')


class EnteredRateWinsTests(TaxRateInputTestCase):
    """Verification 2: an import and an overseas invoice are ordinary."""

    def test_a_rate_that_is_not_the_workspaces_is_kept(self):
        """The default is a starting value, never a constraint."""
        for name, url, payload, path in self.entry_points(tax_rate='9.0000'):
            with self.subTest(line=name):
                created = self.create(url, payload)
                self.assertEqual(read_rate(created, path), '9.0000')

    def test_an_explicit_zero_is_not_filled_in_over(self):
        """A rate of zero entered on purpose is an answer, not an omission."""
        overrides = {'tax_rate': '0.0000', 'tax_treatment': 'zero_rated'}
        for name, url, payload, path in self.entry_points(**overrides):
            if name == 'purchase order line':
                # No treatment column, so there is nothing to send it.
                payload['lines'][0].pop('tax_treatment')
            if name == 'stock receipt line':
                payload['lines'][0].update(
                    supplier_cost_incl_tax='10.0000',
                    input_tax_source='none',
                    input_tax_amount='0.0000',
                )
            if name == 'seed packet receipt':
                payload.update(
                    supplier_cost_incl_tax='6.0000',
                    input_tax_source='none',
                    input_tax_amount='0.0000',
                )
            if name == 'supplier invoice line':
                payload['lines'][0].update(
                    tax_total='0.0000', total_incl_tax='10.0000',
                )
            with self.subTest(line=name):
                created = self.create(url, payload)
                self.assertEqual(read_rate(created, path), '0.0000')

    def test_converting_a_requisition_keeps_the_rate_it_is_given(self):
        """The overseas order raised straight off a need."""
        response = self.convert(self.review_a_requisition(), tax_rate='9.0000')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['lines'][0]['tax_rate'], '9.0000')


class NonStandardTreatmentTests(TaxRateInputTestCase):
    """Verification 3: only a standard-rated supply carries a rate."""

    def test_a_sales_line_classified_away_from_the_standard_rate_stays_at_zero(self):
        """`sales_line_tax_treatment_matches_rate` is a check constraint."""
        for treatment in ('zero_rated', 'exempt', 'out_of_scope', 'unclassified'):
            with self.subTest(treatment=treatment):
                created = self.create(
                    '/sales/order-lines/',
                    self.sales_line_payload(tax_treatment=treatment),
                )
                self.assertEqual(created['tax_rate'], '0.0000')
                self.assertEqual(created['tax_treatment'], treatment)

    def test_an_unclassified_receipt_line_is_not_given_a_rate(self):
        """`unknown` says nobody has classified the supply, not that it is 15%."""
        created = self.create('/inventory/receipts/', self.receipt_payload(
            tax_treatment='unknown',
            supplier_cost_incl_tax='10.0000',
            input_tax_source='none',
            input_tax_amount='0.0000',
        ))

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')
        self.assertEqual(
            StockReceiptLine.objects.get().tax_treatment,
            StockReceiptLine.TaxTreatment.UNKNOWN,
        )

    def test_a_zero_rated_receipt_line_is_not_given_a_rate(self):
        """An import is exactly what the operator is entering here."""
        created = self.create('/inventory/receipts/', self.receipt_payload(
            tax_treatment='zero_rated',
            supplier_cost_incl_tax='10.0000',
            input_tax_source='none',
            input_tax_amount='0.0000',
        ))

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')

    def test_an_unclassified_invoice_line_is_not_given_a_rate(self):
        """The supplier's bill says nothing about the treatment, so neither do we."""
        created = self.create('/purchasing/invoices/', self.invoice_payload(
            tax_treatment='unknown', tax_total='0.0000', total_incl_tax='10.0000',
        ))

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')
        self.assertEqual(SupplierInvoiceLine.objects.get().tax_rate, ZERO)

    def test_an_exempt_packet_receipt_is_not_given_a_rate(self):
        """The seed draft writes a receipt line and follows the same rule."""
        created = self.create('/seeds/packet-receipts/', self.packet_payload(
            tax_treatment='exempt',
            supplier_cost_incl_tax='6.0000',
            input_tax_source='none',
            input_tax_amount='0.0000',
        ))

        self.assertEqual(created['tax_rate'], '0.0000')


class UnstatedTreatmentTests(TaxRateInputTestCase):
    """A request that names no treatment at all takes the model's own.

    This is what `unstated_tax_treatment` is for. Every other test here states
    a treatment, so without this class the attribute would be carried by three
    serializers and exercised by none of them: a receipt line, an invoice line
    and a seed packet all store `unknown` when nobody says otherwise, and
    `unknown` is a positive statement that the supply has not been classified
    rather than an invitation to charge the ordinary rate against it.
    """

    def test_a_receipt_line_naming_no_treatment_is_unknown_and_unrated(self):
        """The line the receiving form posts before anybody classifies it."""
        payload = self.receipt_payload(
            supplier_cost_incl_tax='10.0000',
            input_tax_source='none',
            input_tax_amount='0.0000',
        )
        payload['lines'][0].pop('tax_treatment')

        created = self.create('/inventory/receipts/', payload)

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')
        self.assertEqual(created['lines'][0]['tax_treatment'], 'unknown')

    def test_an_invoice_line_naming_no_treatment_is_unknown_and_unrated(self):
        """Unrated even though the bill did charge tax, which is the point."""
        payload = self.invoice_payload()
        payload['lines'][0].pop('tax_treatment')

        created = self.create('/purchasing/invoices/', payload)

        self.assertEqual(created['lines'][0]['tax_rate'], '0.0000')
        self.assertEqual(created['lines'][0]['tax_treatment'], 'unknown')
        self.assertEqual(created['lines'][0]['tax_total'], '1.5000')

    def test_a_packet_receipt_naming_no_treatment_is_unknown_and_unrated(self):
        """The seed draft answers the same way the receipt line behind it does."""
        payload = self.packet_payload(
            supplier_cost_incl_tax='6.0000',
            input_tax_source='none',
            input_tax_amount='0.0000',
        )
        payload.pop('tax_treatment')

        created = self.create('/seeds/packet-receipts/', payload)

        self.assertEqual(created['tax_rate'], '0.0000')
        self.assertEqual(created['tax_treatment'], 'unknown')


class StoredDocumentsDoNotMoveTests(TaxRateInputTestCase):
    """Verification 4: the rate is filled in, not looked up afterwards."""

    def setUp(self):
        super().setUp()
        self.stored = [
            (name, f"{url}{self.create(url, payload)['pk']}/", payload, path)
            for name, url, payload, path in self.entry_points()
        ]
        self.workspace.default_tax_rate = LATER_RATE
        self.workspace.save()

    def test_every_stored_line_still_reads_the_rate_it_was_filed_at(self):
        """Each document stores the rate it used, which is why there is no history."""
        self.assertEqual(get_current_workspace().default_tax_rate, LATER_RATE)
        for name, url, _payload, path in self.stored:
            with self.subTest(line=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(read_rate(response.data, path), RATE)

    def test_the_columns_themselves_are_unmoved(self):
        """Read from the database rather than from a serializer that could recompute."""
        self.assertEqual(SalesOrderLine.objects.get().tax_rate, Decimal('15'))
        self.assertEqual(PurchaseOrderLine.objects.get().tax_rate, Decimal('15'))
        self.assertEqual(SupplierInvoiceLine.objects.get().tax_rate, Decimal('15'))
        self.assertEqual(
            sorted(str(line.tax_rate) for line in StockReceiptLine.objects.all()),
            ['15.0000', '15.0000'],
        )

    def assert_no_line_took_the_later_rate(self):
        """The rate the workspace moved to appears on nothing already stored."""
        later = f'{LATER_RATE:.4f}'
        for model in (
            SalesOrderLine, PurchaseOrderLine, SupplierInvoiceLine, StockReceiptLine,
        ):
            rates = [str(line.tax_rate) for line in model.objects.all()]
            self.assertNotIn(later, rates, model.__name__)

    def test_a_partial_edit_leaves_a_stored_rate_alone(self):
        """A write about something else does not revisit the rate.

        The rate is filled in when the line is created and never looked up
        again, so editing a supplier reference, a price or a lot number after
        the workspace has moved to 25% leaves the line at the 15% it was filed
        at.
        """
        for name, url, _payload, path in self.stored:
            if name not in PARTIAL_EDIT:
                continue
            with self.subTest(line=name):
                response = self.client.patch(url, PARTIAL_EDIT[name], format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(read_rate(response.data, path), RATE)
        self.assert_no_line_took_the_later_rate()

    def test_re_sending_a_whole_document_does_not_re_rate_its_lines(self):
        """The guard asks the root serializer, not the child reused per line.

        A nested line is validated by a child serializer constructed once with
        no instance of its own and reused for every line, so a child asking
        `self.instance` would answer "this is a create" on every update. The
        fill would then run again on a document a client merely re-sent, and
        every line in it would be re-rated at whatever the workspace charges
        today -- which is exactly what changing the default is not allowed to
        do.

        Both of these replace their lines wholesale, so a body that named no
        rate the first time names none the second either, and the recreated
        line falls back to its column's own default rather than keeping the
        15% the deleted row carried. That is the pre-existing behaviour of an
        omitted rate on an update; what matters here is that it is never 25%.
        """
        for name, url, payload, path in self.stored:
            if name not in REPLACED_WHOLE:
                continue
            with self.subTest(line=name):
                response = self.client.put(url, payload, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(read_rate(response.data, path), '0.0000')
        self.assert_no_line_took_the_later_rate()

    def test_the_next_line_takes_the_new_rate(self):
        """What a changed default does change is the line entered after it."""
        created = self.create('/sales/order-lines/', self.sales_line_payload())

        self.assertEqual(created['tax_rate'], '25.0000')
