"""Tests for maturity metadata anchors in current garden summaries."""

from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from plantings.models import (
    GardenSquareTransplant,
    SpecificPlantLocation,
)
from tests.factories import (
    make_garden_square,
    make_garden_square_sowing,
    make_plant_variety,
    make_seed_packet,
    make_seed_tray_cell_planting,
    make_seed_tray_planting,
    make_seeds,
    make_specific_plant,
)


class GardenMaturityDateTests(TestCase):
    """Current garden dates use the variety's effective maturity basis."""

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_user(username='maturity-tester')
        )
        self.variety = make_plant_variety(
            maturity_days_min=10,
            maturity_days_max=12,
        )
        self.packet = make_seed_packet(
            seeds=make_seeds(plant_variety=self.variety),
        )
        self.square = make_garden_square()

    @staticmethod
    def _response_datetime(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    def _result(self):
        response = self.client.get('/plantings/garden/squares/current/')
        self.assertEqual(response.status_code, 200)
        return response.json()['plantings'][0]

    def _tray_planting(self, planted):
        sowing = make_seed_tray_planting(
            seeds_used=self.packet,
            planted=planted,
            quantity=1,
        )
        allocation = make_seed_tray_cell_planting(
            seed_tray_planting=sowing,
            quantity=1,
        )
        return sowing, allocation

    def test_direct_sow_always_counts_maturity_from_sowing(self):
        """A crop sown in its final bed has no separate transplant anchor."""
        self.variety.maturity_basis = 'transplanting'
        self.variety.save(update_fields=['maturity_basis'])
        sowed_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        make_garden_square_sowing(
            seeds_used=self.packet,
            quantity=1,
            location=self.square,
            planted=sowed_at,
        )

        result = self._result()

        self.assertEqual(
            self._response_datetime(result['maturity_date_early']),
            sowed_at + timedelta(days=10),
        )
        self.assertEqual(
            self._response_datetime(result['maturity_date_late']),
            sowed_at + timedelta(days=12),
        )

    def test_aggregate_transplant_uses_configured_maturity_anchor(self):
        """Aggregate garden transplants use either sowing or transplanting."""
        sowed_at = datetime(2026, 3, 1, 9, tzinfo=datetime_timezone.utc)
        transplanted_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        sowing, _allocation = self._tray_planting(sowed_at)
        GardenSquareTransplant.objects.create(
            original_planting=sowing,
            transplanted=transplanted_at,
            quantity=1,
            location=self.square,
        )

        for basis, anchor in (
            ('seed', sowed_at),
            ('transplanting', transplanted_at),
        ):
            with self.subTest(basis=basis):
                self.variety.maturity_basis = basis
                self.variety.save(update_fields=['maturity_basis'])
                self.assertEqual(
                    self._response_datetime(self._result()['maturity_date_early']),
                    anchor + timedelta(days=10),
                )

    def test_specific_plant_uses_configured_maturity_anchor(self):
        """Individual garden plants share the effective basis rules."""
        sowed_at = datetime(2026, 3, 1, 9, tzinfo=datetime_timezone.utc)
        transplanted_at = datetime(2026, 4, 1, 9, tzinfo=datetime_timezone.utc)
        _sowing, allocation = self._tray_planting(sowed_at)
        plant = make_specific_plant(cell_planting=allocation)
        SpecificPlantLocation.objects.create(
            specific_plant=plant,
            location_type=SpecificPlantLocation.GARDEN_SQUARE,
            garden_square=self.square,
            started=transplanted_at,
        )

        for basis, anchor in (
            ('seed', sowed_at),
            ('transplanting', transplanted_at),
        ):
            with self.subTest(basis=basis):
                self.variety.maturity_basis = basis
                self.variety.save(update_fields=['maturity_basis'])
                self.assertEqual(
                    self._response_datetime(self._result()['maturity_date_early']),
                    anchor + timedelta(days=10),
                )
