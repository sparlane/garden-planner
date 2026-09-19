"""PostgreSQL proof that a loss and a dispatch of one held plant queue (task 125).

Every sales service takes the order before the plants it promises. A loss that
ends a hold takes the order too, so it has to take it first as well; taking
the plant first and the order second lets a cull and a dispatch each hold
what the other is waiting for, and PostgreSQL aborts one as a deadlock.
"""

# pylint: disable=duplicate-code,missing-function-docstring

from concurrent.futures import ThreadPoolExecutor
import threading
import time
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections
from django.test import skipUnlessDBFeature

from plantings import lifecycle
from plantings.lifecycle import (
    EventType,
    LifecycleState,
    OutcomeRequest,
    plant_lifecycle_summary,
    record_germination_event,
    record_lifecycle_event,
)
from plantings.models import SpecificPlant
from tests.factories import make_specific_plant, reserve_plants
from workspaces.models import Workspace

from .commerce import post_fulfillment
from .models import SalesOrder, SalesOrderAllocation
from .test_concurrency import ReservationConcurrencyTestCase


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentLossAndDispatchTests(ReservationConcurrencyTestCase):
    """A cull that holds the plant cannot deadlock the dispatch of its hold."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=settings.CURRENT_WORKSPACE_ID)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        self.user = get_user_model().objects.create_user(username='loss-racer')
        self.plant = make_specific_plant(workspace=self.workspace)
        record_germination_event(self.plant, self.user)
        record_lifecycle_event(self.plant, self.user, OutcomeRequest(EventType.READY))
        order, (allocation,) = reserve_plants(self.workspace, self.user, [self.plant])
        self.order_pk = order.pk
        self.allocation_pk = allocation.pk
        self.plant_locked = threading.Event()

    def _cull(self):
        close_old_connections()
        plant = SpecificPlant.objects.get(pk=self.plant.pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            record_lifecycle_event(
                plant, user, OutcomeRequest(EventType.CULLED, reason='Botrytis.'),
            )
        except OperationalError:
            result = 'deadlock'
        else:
            result = 'culled'
        close_old_connections()
        return result

    def _dispatch(self):
        close_old_connections()
        self.assertTrue(self.plant_locked.wait(10))
        order = SalesOrder.objects.get(pk=self.order_pk)
        user = get_user_model().objects.get(pk=self.user.pk)
        try:
            post_fulfillment(
                order, user, operation_key=uuid4(), allocation_ids=[self.allocation_pk],
            )
        except ValidationError:
            result = 'rejected'
        except OperationalError:
            result = 'deadlock'
        else:
            result = 'dispatched'
        close_old_connections()
        return result

    def test_the_cull_takes_the_order_first_and_the_dispatch_waits(self):
        original = lifecycle._lock_plant  # pylint: disable=protected-access

        def lock_then_linger(plant):
            locked = original(plant)
            if not self.plant_locked.is_set():
                # The dispatch starts now and must be blocked on something
                # the cull holds before the cull reaches for anything more.
                self.plant_locked.set()
                time.sleep(0.5)
            return locked

        with mock.patch.object(lifecycle, '_lock_plant', lock_then_linger):
            with ThreadPoolExecutor(max_workers=2) as pool:
                culled = pool.submit(self._cull)
                dispatched = pool.submit(self._dispatch)
                results = (culled.result(), dispatched.result())

        self.assertEqual(results, ('culled', 'rejected'))
        self.assertEqual(
            SalesOrderAllocation.objects.get(pk=self.allocation_pk).status,
            SalesOrderAllocation.Status.RELEASED,
        )
        self.assertEqual(plant_lifecycle_summary(self.plant).state, LifecycleState.CULLED)
