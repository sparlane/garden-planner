"""Pin the HTTP contract for reading and appending GST arrangements.

There is no JavaScript test runner in this repository, so the shape the
settings screen consumes is pinned here rather than in the frontend. That
includes the method list: an arrangement that could be PATCHed would be an
arrangement that could be rewritten.
"""

# pylint: disable=duplicate-code

from datetime import date

from workspaces.models import Workspace

from tests.api import RESTContractTestCase

from .models import GstRegistration
from .services import record_registration


URL = '/tax/gst/registrations/'
STATUS_URL = '/tax/gst/status/'

# The wire shape the settings screen posts: plain strings, not model enums.
REGISTERED = {
    'registered': True,
    'gst_number': '123456785',
    'basis': 'invoice',
    'filing_frequency': 'two_monthly',
    'period_anchor_month': 3,
}


class GstRegistrationRestTestCase(RESTContractTestCase):
    """Every GST route is a Nursery route, so the fixture is a Nursery."""

    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.get(pk=1)
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()

    def register(self, effective_from=date(2026, 1, 1), **overrides):
        """Record an arrangement directly, for tests about reading them back."""
        values = dict(REGISTERED, effective_from=effective_from)
        values.update(overrides)
        return record_registration(self.workspace, self.user, **values)


class GstRegistrationContractTests(GstRegistrationRestTestCase):
    """The ordinary resource contract, plus the methods deliberately absent."""

    def test_authentication_is_required(self):
        """Tax arrangements are not public information."""
        self.assert_authentication_required([URL, STATUS_URL])

    def test_the_list_is_unpaginated(self):
        """This project serves bare lists; the frontend relies on it."""
        self.register()
        self.assert_list_contract([URL])

    def test_an_arrangement_is_created_and_read_back(self):
        """The create path is the settings screen's only way to record one."""
        self.assert_create_retrieve(
            URL,
            dict(REGISTERED, effective_from='2026-01-01', reason='Voluntary'),
            expected_fields={
                'registered': True,
                'gst_number': '123456785',
                'basis': 'invoice',
                'filing_frequency': 'two_monthly',
                'period_anchor_month': 3,
                'effective_from': '2026-01-01',
                'superseded': False,
            },
        )

    def test_an_arrangement_cannot_be_edited_or_deleted_over_http(self):
        """An editable arrangement would be one that rewrites filed periods."""
        registration = self.register()
        detail = f'{URL}{registration.pk}/'
        for method, payload in (
            (self.client.patch, {'basis': 'payments'}),
            (self.client.put, dict(REGISTERED, effective_from='2026-01-01')),
            (self.client.delete, None),
        ):
            with self.subTest(method=method.__name__):
                response = method(detail, payload, format='json') if payload else method(detail)
                self.assertEqual(response.status_code, 405, response.data)

    def test_a_bad_gst_number_is_reported_against_its_field(self):
        """The settings form highlights the control, so the error must name it."""
        response = self.client.post(
            URL, dict(REGISTERED, gst_number='123456784', effective_from='2026-01-01'),
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('gst_number', response.data)

    def test_backdating_over_a_recorded_arrangement_is_reported(self):
        """The append-only rule has to reach the operator, not just the model."""
        self.register(effective_from=date(2026, 4, 1))
        response = self.client.post(
            URL, dict(REGISTERED, effective_from='2026-01-01'), format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('effective_from', response.data)

    def test_a_superseded_arrangement_is_still_listed(self):
        """Hiding it would leave a changed figure with no visible explanation."""
        wrong = self.register(basis=GstRegistration.Basis.PAYMENTS)
        response = self.client.post(
            URL,
            dict(REGISTERED, effective_from='2026-01-01', supersedes=wrong.pk),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        listed = self.client.get(URL).data
        self.assertEqual(len(listed), 2)
        by_pk = {row['pk']: row for row in listed}
        self.assertTrue(by_pk[wrong.pk]['superseded'])
        self.assertFalse(by_pk[response.data['pk']]['superseded'])


class GstStatusTests(GstRegistrationRestTestCase):
    """One line telling the operator what applies right now."""

    def test_an_unregistered_workspace_reports_no_period(self):
        """Inventing a period here would be the first step to filing a wrong one."""
        response = self.client.get(STATUS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['registered'])
        self.assertFalse(response.data['has_history'])
        self.assertIsNone(response.data['registration'])
        self.assertIsNone(response.data['taxable_period'])

    def test_a_registered_workspace_reports_its_current_period(self):
        """The screen has to name the period a supply recorded today lands in."""
        self.register(effective_from=date(2020, 1, 1))
        response = self.client.get(STATUS_URL)
        self.assertTrue(response.data['registered'])
        period = response.data['taxable_period']
        self.assertEqual(period['frequency'], 'two_monthly')
        self.assertEqual(period['basis'], 'invoice')
        self.assertLessEqual(period['start'], response.data['as_at'])
        self.assertGreaterEqual(period['end'], response.data['as_at'])

    def test_a_deregistered_workspace_reports_its_history(self):
        """"Never registered" and "no longer registered" are different answers."""
        self.register(effective_from=date(2020, 1, 1))
        record_registration(
            self.workspace, self.user, registered=False, effective_from=date(2020, 6, 1),
        )
        response = self.client.get(STATUS_URL)
        self.assertFalse(response.data['registered'])
        self.assertTrue(response.data['has_history'])
        self.assertIsNone(response.data['taxable_period'])


class GardenProfileTests(GstRegistrationRestTestCase):
    """A bookmarked URL must be refused by the server, not only by the menu."""

    def test_garden_mode_is_refused(self):
        """GST arrangements belong to the Nursery profile's commerce workflows."""
        self.workspace.mode = Workspace.Mode.GARDEN
        self.workspace.save()
        for url in (URL, STATUS_URL):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
