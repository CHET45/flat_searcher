from unittest import TestCase

from flat_searcher.transit.geometry import decode_polyline
from flat_searcher.transit.walking import WalkGraph
from journeys_fixture import point

CORNER = point(0, 0)
NORTH = point(0, 300)
EAST = point(300, 300)
GRAPH = WalkGraph.from_data({"nodes": [*CORNER, *NORTH, *EAST], "edges": [0, 1, 1, 2]})


class WalkGraphTests(TestCase):
    def test_a_walk_follows_the_street_around_the_corner(self) -> None:
        walk = GRAPH.field(*point(5, 0), limit_m=2000).to(*point(300, 305))
        self.assertTrue(walk.on_streets)
        self.assertAlmostEqual(walk.m, 610, delta=2)
        self.assertEqual(len(walk.points), 5)
        self.assertEqual(walk.points[1:4], [CORNER, NORTH, EAST])
        rounded = [(round(lat, 5), round(lon, 5)) for lat, lon in (CORNER, NORTH, EAST)]
        self.assertEqual(decode_polyline(walk.encoded)[1:4], rounded)

    def test_far_from_any_street_the_walk_falls_back_to_the_straight_line(self) -> None:
        start = point(0, -400)
        walk = GRAPH.field(*start, limit_m=2000).to(*EAST)
        self.assertFalse(walk.on_streets)
        self.assertAlmostEqual(walk.m, 1.3 * 761, delta=3)
        self.assertEqual(walk.points, [start, EAST])

    def test_beyond_the_search_limit_the_walk_falls_back(self) -> None:
        walk = GRAPH.field(*CORNER, limit_m=400).to(*EAST)
        self.assertFalse(walk.on_streets)
        self.assertEqual(walk.points, [CORNER, EAST])

    def test_a_field_serves_several_destinations(self) -> None:
        field = GRAPH.field(*CORNER, limit_m=2000)
        self.assertAlmostEqual(field.to(*NORTH).m, 300, delta=1)
        self.assertAlmostEqual(field.to(*EAST).m, 600, delta=1)
