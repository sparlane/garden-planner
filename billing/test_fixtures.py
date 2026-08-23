"""A confirmed, dispatchable nursery order the document tests can build on.

Every document scenario needs the same six steps — a registered workspace, a
ready plant, an order, a line, a reservation and a confirmation — and repeating
them in four test modules would make each one mostly setup. The mixin builds
them through the same services the application uses, so a change in `sales`
that breaks the order lifecycle breaks these tests too rather than sliding past
a hand-built row.
"""

# `setUp` is camel case because unittest calls it that, and pylint only
# recognises the name inside a TestCase — which a mixin is deliberately not.
# pylint: disable=duplicate-code,invalid-name

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model

from locations.models import Location
from plantings.lifecycle import (
    EventType,
    OutcomeRequest,
    record_germination_event,
    record_lifecycle_event,
)
from sales.commerce import post_fulfillment, record_payment
from sales.models import Customer, SalesOrderLine
from sales.services import allocate_targets, confirm_order, create_order
from tax.services import record_registration
from tests.factories import make_specific_plant
from workspaces.models import Workspace, get_current_workspace


class DocumentScenarioMixin:
    """Build the commerce a taxable supply document is issued against."""

    #: The registration date every scenario is built after, so a document
    #: issued on any plausible date falls inside a taxable period.
    registered_from = date(2026, 1, 1)

    #: A fixed order date, so a test can name a document date rather than
    #: computing one from today and drifting when the calendar does.
    order_date = date(2026, 5, 1)

    def setUp(self):
        """Put a nursery workspace and a dispatch store behind every scenario."""
        super().setUp()
        self.workspace = get_current_workspace()
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.currency_code = 'NZD'
        self.workspace.default_tax_rate = Decimal('15')
        self.workspace.sales_prices_include_tax = False
        self.workspace.legal_name = 'Kowhai Growers Limited'
        self.workspace.trading_name = 'Kowhai Nursery'
        self.workspace.business_address = '12 Seedling Road\nRichmond 7020'
        self.workspace.save()
        if not hasattr(self, 'user') or self.user is None:
            self.user = get_user_model().objects.create_user(username='billing-user')
        self.store = Location.objects.create(
            workspace=self.workspace, name='Dispatch store', code='BILLING-DISPATCH',
            location_type=Location.LocationType.STORAGE,
        )

    def register_for_gst(self, **overrides):
        """Record the arrangement a taxable supply document is issued under."""
        values = {
            'registered': True,
            'effective_from': self.registered_from,
            'gst_number': '049091850',
            'basis': 'invoice',
            'filing_frequency': 'two_monthly',
            'period_anchor_month': 2,
        }
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)

    def make_customer(self, **overrides):
        """Create a customer a large supply can be identified against."""
        values = {
            'name': 'Riverbend Landscapes',
            'email': 'accounts@riverbend.example',
            'billing_address': '8 Quarry Road\nBrightwater 7022',
        }
        values.update(overrides)
        return Customer.objects.create(workspace=self.workspace, **values)

    def ready_plant(self, batch=None, cell_planting=None, ready_at=None):
        """Grow one plant as far as saleable.

        `ready_at` back-dates the germination and the readiness, which a test
        needs whenever it also wants to name the dispatch date: lifecycle
        events must be recorded in the order they happened, so a plant that
        became ready today cannot have been dispatched in June.
        """
        values = {'workspace': self.workspace}
        if batch is not None:
            values['batch'] = batch
        if cell_planting is not None:
            values['cell_planting'] = cell_planting
        if ready_at is not None:
            values['germinated'] = ready_at
        plant = make_specific_plant(**values)
        record_germination_event(plant, self.user)
        record_lifecycle_event(
            plant, self.user, OutcomeRequest(EventType.READY, occurred_at=ready_at),
        )
        return plant

    def ready_plants(self, count, ready_at=None):
        """Grow a sibling group, so one order line can cover all of them."""
        first = self.ready_plant(ready_at=ready_at)
        rest = [
            self.ready_plant(
                batch=first.batch, cell_planting=first.cell_planting, ready_at=ready_at,
            )
            for _ in range(count - 1)
        ]
        return [first, *rest]

    def confirmed_order(self, plants, customer=None, unit_price='10.0000', tax_rate='15', **overrides):
        """Create, allocate and confirm one order covering every plant given."""
        overrides.setdefault('order_date', self.order_date)
        order = create_order(self.workspace, self.user, customer=customer, **overrides)
        line = SalesOrderLine(
            order=order,
            line_type=SalesOrderLine.LineType.SEEDLING,
            variety=plants[0].batch.variety,
            description='Hebe "Wiri Charm" 2L',
            quantity=len(plants),
            unit_price=Decimal(unit_price),
            tax_rate=Decimal(tax_rate),
        )
        line.save()
        allocations = allocate_targets(
            line, self.user, [plant.pk for plant in plants], (),
        )
        confirm_order(order, self.user)
        order.refresh_from_db()
        line.refresh_from_db()
        return order, line, allocations

    def fulfill(self, order, allocations, fulfilled_at=None):
        """Dispatch the allocations given, returning the posted fulfillment."""
        return post_fulfillment(
            order, self.user,
            operation_key=uuid4(),
            allocation_ids=[allocation.pk for allocation in allocations],
            fulfilled_at=fulfilled_at,
        )

    def pay(self, order, amount, paid_on):
        """Record cash against an order, which is how a deposit is taken."""
        return record_payment(
            order, self.user,
            operation_key=uuid4(),
            paid_on=paid_on,
            amount=Decimal(amount),
            method='bank_transfer',
        )
