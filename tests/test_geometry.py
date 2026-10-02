from unittest import TestCase

from flat_searcher.transit.geometry import (
    decode_polyline,
    distance_m,
    encode_polyline,
    simplify,
    walk_minutes,
)


class PolylineTests(TestCase):
    def test_encoding_matches_the_published_reference(self) -> None:
        points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
        self.assertEqual(encode_polyline(points), "_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertEqual(decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@"), points)

    def test_round_trip_keeps_five_decimals(self) -> None:
        points = [(56.94608, 24.1302), (56.94612, 24.13031), (56.9459, 24.1301)]
        self.assertEqual(decode_polyline(encode_polyline(points)), points)


class SimplifyTests(TestCase):
    def test_keeps_the_ends_and_drops_points_within_tolerance(self) -> None:
        line = [(56.9, 24.1), (56.90001, 24.101), (56.9, 24.102)]
        self.assertEqual(simplify(line, tolerance_m=3), [(56.9, 24.1), (56.9, 24.102)])

    def test_keeps_a_point_that_bends_the_line(self) -> None:
        line = [(56.9, 24.1), (56.901, 24.101), (56.9, 24.102)]
        self.assertEqual(simplify(line, tolerance_m=3), line)


class WalkingTests(TestCase):
    def test_walking_takes_a_road_detour_at_five_km_per_hour(self) -> None:
        self.assertAlmostEqual(walk_minutes(750), 11.7, places=1)

    def test_distance_is_haversine(self) -> None:
        self.assertAlmostEqual(distance_m(56.9, 24.1, 56.9, 24.1), 0.0)
        self.assertAlmostEqual(distance_m(56.9, 24.1, 56.91, 24.1), 1112, delta=1)
