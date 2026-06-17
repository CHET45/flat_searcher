from unittest import TestCase

from flat_searcher.geo import (
    AddressPrecision,
    Coordinate,
    GeocodeConfidence,
    ShopScoreInput,
    TransportScoreInput,
    determine_address_precision,
    haversine_distance_m,
    location_score_eligibility,
    rtu_distance_score,
    shop_score,
    transport_score,
)


class GeoTests(TestCase):
    def test_address_precision_requires_house_number_for_exact_address(self) -> None:
        self.assertEqual(
            determine_address_precision("centrs", "Stabu", "87"),
            AddressPrecision.EXACT_HOUSE,
        )
        self.assertEqual(
            determine_address_precision("centrs", "Stabu", None),
            AddressPrecision.STREET_APPROX,
        )
        self.assertEqual(
            determine_address_precision("centrs", None, None),
            AddressPrecision.DISTRICT_APPROX,
        )
        self.assertEqual(determine_address_precision(None, None, None), AddressPrecision.UNKNOWN)

    def test_location_scores_require_exact_high_confidence_address(self) -> None:
        enabled = location_score_eligibility(
            AddressPrecision.EXACT_HOUSE,
            GeocodeConfidence.HIGH,
        )
        disabled = location_score_eligibility(
            AddressPrecision.STREET_APPROX,
            GeocodeConfidence.HIGH,
        )

        self.assertTrue(enabled.geo_scores_enabled)
        self.assertFalse(disabled.geo_scores_enabled)
        self.assertEqual(
            disabled.disabled_reason,
            "Approximate address - location scores not calculated",
        )

    def test_haversine_distance_is_reasonable_for_riga_points(self) -> None:
        origin = Coordinate(latitude=56.9496, longitude=24.1052)
        destination = Coordinate(latitude=56.9496, longitude=24.1152)

        distance = haversine_distance_m(origin, destination)

        self.assertGreater(distance, 500)
        self.assertLess(distance, 700)

    def test_distance_scores_decrease_with_distance(self) -> None:
        self.assertGreater(rtu_distance_score(1_000) or 0, rtu_distance_score(7_000) or 0)
        self.assertGreater(
            shop_score(ShopScoreInput(nearest_shop_distance_m=200, shops_within_300m=1)) or 0,
            shop_score(ShopScoreInput(nearest_shop_distance_m=1_500)) or 0,
        )
        self.assertGreater(
            transport_score(TransportScoreInput(nearest_stop_distance_m=200, stops_nearby_count=4))
            or 0,
            transport_score(TransportScoreInput(nearest_stop_distance_m=1_200)) or 0,
        )
