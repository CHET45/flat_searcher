from unittest import TestCase

from flat_searcher.geo import AddressPrecision
from flat_searcher.mapping import MapApartmentPoint, MarkerVisualState, build_map_markers


class MappingTests(TestCase):
    def test_build_map_markers_skips_points_without_coordinates(self) -> None:
        markers = build_map_markers(
            (
                MapApartmentPoint(
                    listing_id=1,
                    latitude=56.95,
                    longitude=24.1,
                    address_precision=AddressPrecision.EXACT_HOUSE,
                    score=82,
                ),
                MapApartmentPoint(
                    listing_id=2,
                    latitude=None,
                    longitude=None,
                    address_precision=AddressPrecision.UNKNOWN,
                    score=70,
                ),
            )
        )

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].score_bucket, "high")

    def test_marker_visual_state_reflects_status_and_precision(self) -> None:
        markers = build_map_markers(
            (
                MapApartmentPoint(
                    listing_id=1,
                    latitude=56.95,
                    longitude=24.1,
                    address_precision=AddressPrecision.STREET_APPROX,
                    score=60,
                ),
                MapApartmentPoint(
                    listing_id=2,
                    latitude=56.96,
                    longitude=24.11,
                    address_precision=AddressPrecision.EXACT_HOUSE,
                    score=60,
                    is_favorite=True,
                ),
                MapApartmentPoint(
                    listing_id=3,
                    latitude=56.97,
                    longitude=24.12,
                    address_precision=AddressPrecision.EXACT_HOUSE,
                    score=60,
                    listing_status="inactive",
                ),
            )
        )

        self.assertEqual(markers[0].visual_state, MarkerVisualState.APPROXIMATE)
        self.assertEqual(markers[1].visual_state, MarkerVisualState.FAVORITE)
        self.assertEqual(markers[2].visual_state, MarkerVisualState.INACTIVE)
        self.assertEqual(markers[0].to_dict()["visual_state"], "approximate")
