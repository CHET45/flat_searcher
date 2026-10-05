from unittest import TestCase

from flat_searcher.transit.driving import DriveGraph, Leg
from journeys_fixture import point

A = point(0, 0)
B = point(1000, 0)
C = point(0, 1000)
D = point(1000, 1000)
ISLAND = point(0, -200)
SINK = point(1000, -200)
NODES = [A, B, C, D, ISLAND, point(500, -200), SINK]
EDGES = [
    (0, 1, 1000, 100),
    (1, 3, 1000, 100),
    (3, 1, 1000, 100),
    (3, 2, 1000, 100),
    (2, 3, 1000, 100),
    (2, 0, 1000, 100),
    (0, 2, 1000, 100),
    (0, 3, 1414, 600),
    (3, 0, 1414, 600),
    (4, 5, 500, 50),
    (5, 4, 500, 50),
    (1, 6, 200, 20),
]
GRAPH = DriveGraph.from_data(
    {
        "nodes": [value for node in NODES for value in node],
        "edges": [value for edge in EDGES for value in edge],
    }
)


class DriveGraphTests(TestCase):
    def test_a_one_way_street_makes_there_and_back_differ(self) -> None:
        self.assertEqual(GRAPH.field(*A).at(*B), Leg(100, 1000))
        self.assertEqual(GRAPH.field(*A, reverse=True).at(*B), Leg(300, 3000))
        self.assertEqual(GRAPH.field(*B).at(*A), Leg(300, 3000))
        self.assertEqual(GRAPH.field(*B, reverse=True).at(*A), Leg(100, 1000))

    def test_metres_follow_the_fastest_path_not_the_shortest(self) -> None:
        self.assertEqual(GRAPH.field(*A).at(*D), Leg(200, 2000))
        self.assertEqual(GRAPH.field(*D, reverse=True).node_cost(0), (200.0, 2000.0))

    def test_the_limit_stops_the_search(self) -> None:
        bounded = GRAPH.field(*A, limit_s=150)
        self.assertEqual(bounded.at(*B), Leg(100, 1000))
        self.assertIsNone(bounded.at(*D))
        self.assertIsNone(bounded.node_cost(3))
        self.assertEqual(GRAPH.field(*A, limit_s=100).node_cost(1), (100.0, 1000.0))
        self.assertEqual(GRAPH.field(*A, limit_s=0).node_cost(0), (0.0, 0.0))

    def test_the_limit_counts_the_access_leg(self) -> None:
        west = point(-100, 0)
        self.assertIsNone(GRAPH.field(*west, limit_s=120).at(*B))
        self.assertEqual(GRAPH.field(*west, limit_s=140).at(*B), Leg(131, 1130))

    def test_access_legs_are_added_at_both_ends(self) -> None:
        west, east = point(-100, 0), point(1100, 0)
        self.assertEqual(GRAPH.field(*west).at(*east), Leg(162, 1260))
        self.assertEqual(GRAPH.field(*east, reverse=True).at(*west), Leg(162, 1260))
        self.assertEqual(GRAPH.field(*west, reverse=True).at(*east), Leg(362, 3260))

    def test_points_beyond_400_metres_do_not_snap(self) -> None:
        near, far = point(0, 1390), point(0, 1450)
        snapped = GRAPH.nearest(*near)
        assert snapped is not None
        self.assertEqual(snapped[0], 2)
        self.assertAlmostEqual(snapped[1], 390, delta=1)
        self.assertIsNone(GRAPH.nearest(*far))
        self.assertIsNone(GRAPH.field(*A).at(*far))
        self.assertIsNone(GRAPH.field(*far).at(*A))
        self.assertIsNone(GRAPH.field(*far).node_cost(0))

    def test_a_point_snaps_to_the_nearest_node(self) -> None:
        pair = DriveGraph.from_data(
            {"nodes": [*point(0, 0), *point(200, 0)], "edges": [0, 1, 200, 20, 1, 0, 200, 20]}
        )
        snaps = [pair.nearest(*point(east, 0)) for east in (50, 150)]
        self.assertEqual([snap and snap[0] for snap in snaps], [0, 1])

    def test_snapping_skips_islands_and_dead_ends(self) -> None:
        by_island = GRAPH.nearest(*point(0, -150))
        by_sink = GRAPH.nearest(*point(1000, -180))
        assert by_island is not None and by_sink is not None
        self.assertEqual(by_island[0], 0)
        self.assertEqual(by_sink[0], 1)

    def test_an_unreachable_node_has_no_cost(self) -> None:
        field = GRAPH.field(*A)
        self.assertIsNone(field.node_cost(4))
        self.assertEqual(field.node_cost(6), (120.0, 1200.0))
        self.assertIsNone(GRAPH.field(*A, reverse=True).node_cost(6))


class LegTests(TestCase):
    def test_minutes_round_half_up_and_never_show_zero(self) -> None:
        self.assertEqual([Leg(s, 0).minutes for s in (0, 89, 90, 149, 150)], [1, 1, 2, 2, 3])

    def test_kilometres_keep_one_decimal_rounded_half_up(self) -> None:
        self.assertEqual([Leg(0, m).km for m in (0, 1249, 1250, 14949)], [0.0, 1.2, 1.3, 14.9])
