from unittest import TestCase

from flat_searcher.transit.geometry import decode_polyline
from flat_searcher.transit.osm import OVERPASS_QUERY, reduce_streets, reduce_walk_graph


def _way(way_id: int, tags: dict, points: list[tuple[float, float]]) -> dict:
    return {
        "type": "way",
        "id": way_id,
        "tags": tags,
        "geometry": [{"lat": lat, "lon": lon} for lat, lon in points],
    }


def _member(role: str, points: list[tuple[float, float]]) -> dict:
    return {"type": "way", "ref": 1, "role": role, "geometry": [{"lat": lat, "lon": lon} for lat, lon in points]}


SQUARE = [(56.90, 24.10), (56.90, 24.11), (56.91, 24.11), (56.91, 24.10), (56.90, 24.10)]
RESPONSE = {
    "elements": [
        _way(1, {"highway": "primary"}, [(56.90, 24.10), (56.90, 24.12)]),
        _way(2, {"highway": "trunk_link"}, [(56.90, 24.12), (56.901, 24.121)]),
        _way(3, {"highway": "tertiary"}, [(56.91, 24.10), (56.91, 24.12)]),
        _way(4, {"highway": "living_street"}, [(56.92, 24.10), (56.92, 24.101), (56.92, 24.12)]),
        _way(5, {"highway": "footway"}, [(56.93, 24.10), (56.93, 24.12)]),
        _way(6, {"railway": "rail"}, [(56.94, 24.10), (56.94, 24.12)]),
        _way(7, {"railway": "rail", "service": "siding"}, [(56.95, 24.10), (56.95, 24.12)]),
        _way(8, {"natural": "water"}, SQUARE),
        {
            "type": "relation",
            "id": 9,
            "tags": {"natural": "water"},
            "members": [
                _member("outer", [(56.80, 24.10), (56.80, 24.12), (56.81, 24.12)]),
                _member("outer", [(56.81, 24.12), (56.81, 24.10), (56.80, 24.10)]),
                _member("inner", [(56.803, 24.103), (56.803, 24.105), (56.805, 24.105), (56.803, 24.103)]),
                _member("outer", [(56.70, 24.10), (56.70, 24.12)]),
            ],
        },
    ]
}


class ReduceStreetsTests(TestCase):
    def setUp(self) -> None:
        self.streets = reduce_streets(RESPONSE)

    def test_roads_fall_into_three_tiers_and_footways_are_dropped(self) -> None:
        tiers = [[decode_polyline(line)[0] for line in tier] for tier in self.streets["roads"]]
        self.assertEqual(tiers[0], [(56.90, 24.10), (56.90, 24.12)])
        self.assertEqual(tiers[1], [(56.91, 24.10)])
        self.assertEqual(tiers[2], [(56.92, 24.10)])

    def test_a_straight_run_of_points_is_simplified(self) -> None:
        self.assertEqual(decode_polyline(self.streets["roads"][2][0]), [(56.92, 24.10), (56.92, 24.12)])

    def test_only_main_rail_lines_are_kept(self) -> None:
        self.assertEqual([decode_polyline(line)[0] for line in self.streets["rail"]], [(56.94, 24.10)])

    def test_water_rings_come_from_closed_ways_and_assembled_relations(self) -> None:
        rings = [(role, decode_polyline(line)) for role, line in self.streets["water"]]
        self.assertEqual(rings[0], ("outer", SQUARE))
        outer = next(ring for role, ring in rings[1:] if role == "outer")
        self.assertEqual(outer[0], outer[-1])
        self.assertEqual(len(outer), 5)
        self.assertEqual(sum(1 for role, _ in rings if role == "inner"), 1)
        self.assertEqual(len(rings), 3)

    def test_the_query_covers_riga_and_asks_for_geometry(self) -> None:
        self.assertIn("out geom", OVERPASS_QUERY)
        self.assertIn("(56.85,23.9,57.1,24.35)", OVERPASS_QUERY)


def _topo(way_id: int, tags: dict, nodes: list[int], points: list[tuple[float, float]]) -> dict:
    way = _way(way_id, tags, points)
    way["nodes"] = nodes
    return way


N = {1: (56.90, 24.10), 2: (56.901, 24.10), 3: (56.902, 24.10), 4: (56.902, 24.101), 5: (56.903, 24.101),
     6: (56.901, 24.099), 7: (56.901, 24.102), 8: (56.905, 24.105), 9: (56.906, 24.105)}
WALK_RESPONSE = {
    "elements": [
        _topo(1, {"highway": "footway"}, [1, 2, 3], [N[1], N[2], N[3]]),
        _topo(2, {"highway": "residential"}, [3, 4], [N[3], N[4]]),
        _topo(3, {"highway": "motorway"}, [4, 5], [N[4], N[5]]),
        _topo(4, {"highway": "service", "access": "private"}, [2, 6], [N[2], N[6]]),
        _topo(5, {"highway": "service", "access": "private", "foot": "yes"}, [2, 7], [N[2], N[7]]),
        _topo(6, {"highway": "path", "foot": "no"}, [8, 9], [N[8], N[9]]),
        _way(7, {"natural": "water"}, SQUARE),
    ]
}


class ReduceWalkGraphTests(TestCase):
    def test_walkable_ways_become_compact_nodes_and_edges(self) -> None:
        graph = reduce_walk_graph(WALK_RESPONSE)
        self.assertEqual(graph["nodes"], [*N[1], *N[2], *N[3], *N[4], *N[7]])
        self.assertEqual(graph["edges"], [0, 1, 1, 2, 2, 3, 1, 4])

    def test_the_query_asks_for_every_highway(self) -> None:
        self.assertIn('way["highway"](56.85,23.9,57.1,24.35)', OVERPASS_QUERY)
