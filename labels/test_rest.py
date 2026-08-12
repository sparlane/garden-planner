"""REST contract tests for label resolution and print auditing."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from tests.factories import make_garden_area, make_specific_plant
from workspaces.models import Workspace, get_current_workspace

from health.models import HealthObservation, HealthObservationType
from health.operations import quarantine_observation
from health.services import preview_observation, record_observation

from .models import LabelCode, LabelPrintJob, LabelTemplate
from .services import ensure_identity, replace_code, void_code


class LabelResolutionTests(TestCase):
    """Scans resolve safely without leaking records across workspaces."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('labels', password='secret')
        self.client.force_login(self.user)
        self.workspace = get_current_workspace()
        self.identity = ensure_identity(make_specific_plant())
        self.code = self.identity.codes.get(status=LabelCode.Status.ACTIVE)

    def resolve(self, value):
        """Resolve one scanner value through the public endpoint."""
        return self.client.get('/labels/resolve/', {'value': value})

    def test_bare_code_and_deep_link_resolve_to_the_same_plant(self):
        """Hardware scanners and phone cameras reach the same identity."""
        bare = self.resolve(self.code.code)
        linked = self.resolve(f'https://example.test/#/scan/{self.code.code}')
        self.assertEqual(bare.status_code, 200)
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(bare.data['status'], 'active')
        self.assertEqual(bare.data['target']['object_id'], self.identity.target_object_id)
        self.assertIn('bulk_select', bare.data['capabilities'])
        self.assertEqual(linked.data['code'], bare.data['code'])

    def test_nursery_scan_exposes_health_actions_and_active_release(self):
        """A scan offers only health actions currently valid for its target."""
        self.workspace.mode = Workspace.Mode.NURSERY
        self.workspace.save()
        plant = self.identity.target
        initial = self.resolve(self.code.code)
        self.assertIn('health_inspection', initial.data['capabilities'])
        self.assertIn('health_treatment', initial.data['capabilities'])
        self.assertIn('health_quarantine', initial.data['capabilities'])
        self.assertNotIn('health_release', initial.data['capabilities'])
        scopes = [{'type': 'plant', 'id': plant.pk}]
        preview = preview_observation(self.workspace, scopes)
        observation = record_observation(
            self.workspace, self.user, scopes=scopes,
            reviewed_digest=preview['digest'],
            observation_type=HealthObservationType.objects.get(
                workspace=self.workspace, code='pest-signs',
            ),
            severity=HealthObservation.Severity.HIGH,
        )
        quarantine_observation(
            self.workspace, self.user, observation,
            idempotency_key=uuid4(), reason='Prevent spread.',
        )
        constrained = self.resolve(self.code.code)
        self.assertIn('health_release', constrained.data['capabilities'])

    def test_unknown_replaced_void_and_wrong_workspace_are_explicit(self):
        """Every unusable scan explains its condition without target leakage."""
        self.assertEqual(self.resolve('PLT-NOT-A-CODE').data['status'], 'unknown')
        old = self.code.code
        replacement = replace_code(self.code, self.user, 'Unreadable')
        replaced = self.resolve(old)
        self.assertEqual(replaced.data['status'], 'replaced')
        self.assertEqual(replaced.data['current_code'], replacement.code)
        void_code(replacement, self.user, 'No longer used')
        self.assertEqual(self.resolve(replacement.code).data['status'], 'inactive')

        other = Workspace.objects.create(name='Other labels')
        foreign = ensure_identity(make_garden_area(workspace=other)).codes.get()
        wrong = self.resolve(foreign.code)
        self.assertEqual(wrong.data, {
            'status': 'wrong_workspace',
            'message': 'This code belongs to another workspace.',
        })


class LabelPrintJobTests(TestCase):
    """Print previews and initiated jobs have distinct audit behavior."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('printer', password='secret')
        self.client.force_login(self.user)
        self.identity = ensure_identity(make_garden_area())
        self.template = LabelTemplate.objects.get(
            workspace=get_current_workspace(),
            name='Single QR 100 × 50 mm',
        )

    def request(self):
        """Return one valid QR print selection."""
        return {'template': self.template.pk, 'identities': [self.identity.pk], 'payload_mode': 'url'}

    def test_preview_is_not_audit_but_print_job_and_print_click_are(self):
        """Only initiated physical work enters immutable print history."""
        preview = self.client.post('/labels/print-jobs/preview/', self.request(), content_type='application/json')
        self.assertEqual(preview.status_code, 200)
        self.assertIsNone(preview.data['job'])
        self.assertEqual(LabelPrintJob.objects.count(), 0)

        created = self.client.post('/labels/print-jobs/', self.request(), content_type='application/json')
        self.assertEqual(created.status_code, 201)
        self.assertIn('/#/scan/', created.data['items'][0]['payload'])
        printed = self.client.post(f"/labels/print-jobs/{created.data['job']}/printed/", {}, content_type='application/json')
        self.assertEqual(printed.status_code, 200)
        self.assertIsNotNone(printed.data['printed_at'])

        reprint = self.client.post('/labels/print-jobs/preview/', self.request(), content_type='application/json')
        self.assertTrue(reprint.data['items'][0]['is_reprint'])

    def test_code128_rejects_a_url_payload(self):
        """Linear labels remain compact enough for practical scanners."""
        template = LabelTemplate.objects.get(
            workspace=get_current_workspace(),
            name='Roll Code 128 50 × 30 mm',
        )
        response = self.client.post(
            '/labels/print-jobs/preview/',
            {'template': template.pk, 'identities': [self.identity.pk], 'payload_mode': 'url'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('payload_mode', response.data)
