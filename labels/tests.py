"""Behavioral tests for stable label identities and code lifecycle."""

from importlib import import_module

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase

from tests.factories import (
    make_garden_area,
    make_location,
    make_production_batch,
    make_seed_tray,
    make_specific_plant,
)

from .models import LabelCode, LabelIdentity
from .services import ensure_identity, replace_code, valid_code, void_code


class LabelIssuanceTests(TestCase):
    """Every supported object receives one stable code automatically."""

    def test_supported_targets_receive_valid_unique_codes(self):
        """Every currently label-worthy model receives its typed checked code."""
        targets = [
            make_specific_plant(),
            make_seed_tray(),
            make_production_batch(),
            make_location(),
            make_garden_area(),
        ]
        codes = []
        for target in targets:
            identity = ensure_identity(target)
            code = identity.codes.get(status=LabelCode.Status.ACTIVE)
            self.assertTrue(valid_code(code.code))
            codes.append(code.code)
        self.assertEqual(len(codes), len(set(codes)))

    def test_repeated_issuance_reuses_the_same_identity_and_code(self):
        """Saving or asking again must never silently rotate physical identity."""
        plant = make_specific_plant()
        first = ensure_identity(plant)
        original = first.codes.get().code
        second = ensure_identity(plant)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.codes.get().code, original)
        self.assertEqual(
            LabelIdentity.objects.filter(
                target_content_type=first.target_content_type,
                target_object_id=plant.pk,
            ).count(),
            1,
        )

    def test_the_data_backfill_is_safe_when_another_migration_test_reapplies_it(self):
        """Other apps' migration tests can legitimately execute this migration twice."""
        make_specific_plant()
        before = (LabelIdentity.objects.count(), LabelCode.objects.count())
        migration = import_module('labels.migrations.0002_backfill_labels_and_templates')
        migration.backfill(apps, None)
        migration.backfill(apps, None)
        self.assertEqual(
            (LabelIdentity.objects.count(), LabelCode.objects.count()),
            before,
        )


class LabelCodeLifecycleTests(TestCase):
    """Replacement and void actions retain the exact code operators saw."""

    def setUp(self):
        self.identity = ensure_identity(make_garden_area())
        self.code = self.identity.codes.get()

    def test_replace_preserves_the_old_code_and_issues_a_new_one(self):
        """A damaged label remains resolvable while its successor becomes active."""
        replacement = replace_code(self.code, None, 'Damaged label')
        self.code.refresh_from_db()
        self.assertEqual(self.code.status, LabelCode.Status.REPLACED)
        self.assertEqual(self.code.replacement, replacement)
        self.assertEqual(replacement.identity, self.identity)
        self.assertNotEqual(replacement.code, self.code.code)

    def test_void_requires_a_reason(self):
        """An identity cannot disappear from operational use without an audit reason."""
        with self.assertRaises(ValidationError):
            void_code(self.code, None, '')
        void_code(self.code, None, 'Object was labelled in error')
        self.code.refresh_from_db()
        self.assertEqual(self.code.status, LabelCode.Status.VOID)
