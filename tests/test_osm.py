from unittest import TestCase

from flat_searcher.transit.geometry import Point, decode_polyline, encode_polyline
from flat_searcher.transit.osm import (
    OVERPASS_QUERY,
    PLACES_QUERY,
    reduce_drive_graph,
    reduce_places,
    reduce_streets,
    reduce_walk_graph,
)


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


def _road(tags: dict, nodes: list[int], way_id: int = 1) -> dict:
    return _topo(way_id, tags, nodes, [(56.9 + 0.001 * (node % 100), 24.1 + 0.01 * (node // 100)) for node in nodes])


def _pairs(graph: dict) -> set[tuple[int, int]]:
    edges = graph["edges"]
    return {(edges[i], edges[i + 1]) for i in range(0, len(edges), 4)}


def _drive(tags: dict) -> set[tuple[int, int]]:
    return _pairs(reduce_drive_graph({"elements": [_road(tags, [1, 2])]}))


BOTH = {(0, 1), (1, 0)}


class ReduceDriveGraphTests(TestCase):
    def test_a_two_way_street_becomes_two_directed_edges_in_metres_and_seconds(self) -> None:
        way = _topo(1, {"highway": "residential"}, [7, 9], [(56.90000041, 24.1), (56.901, 24.1)])
        graph = reduce_drive_graph({"elements": [way]})
        self.assertEqual(graph["nodes"], [56.9, 24.1, 56.901, 24.1])
        self.assertEqual(graph["edges"], [0, 1, 111, 20, 1, 0, 111, 20])

    def test_oneway_roundabouts_and_motorways_keep_one_direction(self) -> None:
        cases = [
            ({"oneway": "yes"}, {(0, 1)}),
            ({"oneway": "true"}, {(0, 1)}),
            ({"oneway": "1"}, {(0, 1)}),
            ({"oneway": "-1"}, {(1, 0)}),
            ({"oneway": "no"}, BOTH),
            ({"junction": "roundabout"}, {(0, 1)}),
            ({"junction": "circular"}, {(0, 1)}),
            ({"junction": "roundabout", "oneway": "no"}, BOTH),
            ({"highway": "motorway"}, {(0, 1)}),
            ({"highway": "motorway_link"}, {(0, 1)}),
            ({"highway": "motorway", "oneway": "no"}, BOTH),
            ({"highway": "motorway", "oneway": "-1"}, {(1, 0)}),
            ({"highway": "trunk"}, BOTH),
        ]
        for extra, expected in cases:
            with self.subTest(extra):
                self.assertEqual(_drive({"highway": "residential", **extra}), expected)

    def test_roads_cars_may_not_use_are_left_out(self) -> None:
        for tags in (
            {"highway": "footway"},
            {"highway": "pedestrian"},
            {"highway": "track"},
            {"highway": "residential_link"},
            {"highway": "service", "service": "parking_aisle"},
            {"highway": "service", "service": "drive-through"},
            {"highway": "service", "service": "emergency_access"},
            {"highway": "residential", "access": "no"},
            {"highway": "residential", "access": "private"},
            {"highway": "residential", "motor_vehicle": "no"},
            {"highway": "residential", "motorcar": "private"},
            {"highway": "residential", "access": "yes", "motor_vehicle": "no"},
            {"highway": "service", "access": "psv"},
            {"highway": "residential", "motor_vehicle": "bus"},
            {"railway": "rail"},
        ):
            with self.subTest(tags):
                self.assertEqual(_drive(tags), set())

    def test_a_motor_tag_reopens_a_closed_road(self) -> None:
        for tags in (
            {"highway": "service", "service": "driveway"},
            {"highway": "residential", "access": "destination"},
            {"highway": "residential", "access": "no", "motor_vehicle": "yes"},
            {"highway": "residential", "access": "private", "motorcar": "destination"},
            {"highway": "residential", "motor_vehicle": "private", "motorcar": "yes"},
        ):
            with self.subTest(tags):
                self.assertEqual(_drive(tags), BOTH)

    def test_ways_without_matching_geometry_and_other_elements_are_ignored(self) -> None:
        response = {
            "elements": [
                _topo(1, {"highway": "residential"}, [1, 2], [(56.9, 24.1)]),
                _topo(2, {"highway": "residential"}, [3], [(56.9, 24.1)]),
                {"type": "relation", "id": 3, "tags": {"highway": "residential"}, "members": []},
            ]
        }
        self.assertEqual(reduce_drive_graph(response), {"nodes": [], "edges": []})

    def test_free_flow_speed_follows_the_class_links_and_the_speed_limit(self) -> None:
        cases = [
            ({"highway": "motorway", "oneway": "no"}, 57),
            ({"highway": "trunk"}, 73),
            ({"highway": "primary"}, 100),
            ({"highway": "secondary"}, 114),
            ({"highway": "tertiary"}, 133),
            ({"highway": "unclassified"}, 160),
            ({"highway": "residential"}, 200),
            ({"highway": "living_street"}, 400),
            ({"highway": "service"}, 334),
            ({"highway": "motorway_link"}, 71),
            ({"highway": "trunk_link"}, 91),
            ({"highway": "primary_link"}, 125),
            ({"highway": "secondary_link"}, 143),
            ({"highway": "tertiary_link"}, 167),
            ({"highway": "residential", "maxspeed": "20"}, 250),
            ({"highway": "primary_link", "maxspeed": "30"}, 167),
            ({"highway": "primary", "maxspeed": "90"}, 100),
            ({"highway": "primary", "maxspeed": "LV:urban"}, 100),
            ({"highway": "primary", "maxspeed": "0"}, 100),
        ]
        for tags, seconds in cases:
            with self.subTest(tags):
                graph = reduce_drive_graph({"elements": [_road(tags, [0, 10])]})
                self.assertEqual(graph["edges"][:4], [0, 1, 1112, seconds])

    def test_a_chain_without_junctions_collapses_into_one_edge_each_way(self) -> None:
        graph = reduce_drive_graph({"elements": [_road({"highway": "service"}, [0, 1, 2, 3])]})
        self.assertEqual(graph["nodes"], [56.9, 24.1, 56.903, 24.1])
        self.assertEqual(graph["edges"], [0, 1, 334, 100, 1, 0, 334, 100])

    def test_junctions_and_way_ends_are_kept(self) -> None:
        response = {
            "elements": [
                _road({"highway": "residential"}, [1, 2, 3, 4, 5], way_id=1),
                _road({"highway": "residential", "oneway": "yes"}, [3, 103, 104], way_id=2),
            ]
        }
        graph = reduce_drive_graph(response)
        self.assertEqual(graph["nodes"], [56.901, 24.1, 56.903, 24.1, 56.905, 24.1, 56.904, 24.11])
        self.assertEqual(
            graph["edges"], [0, 1, 222, 40, 1, 0, 222, 40, 1, 2, 222, 40, 2, 1, 222, 40, 1, 3, 718, 129]
        )

    def test_even_a_metre_of_road_takes_a_second(self) -> None:
        way = _topo(1, {"highway": "motorway"}, [1, 2], [(56.9, 24.1), (56.9, 24.10001)])
        self.assertEqual(reduce_drive_graph({"elements": [way]})["edges"], [0, 1, 1, 1])

    def test_of_two_ways_between_the_same_junctions_the_faster_one_counts(self) -> None:
        response = {
            "elements": [
                _road({"highway": "residential"}, [0, 10], way_id=1),
                _road({"highway": "primary"}, [0, 10], way_id=2),
                _road({"highway": "living_street"}, [0, 10], way_id=3),
            ]
        }
        self.assertEqual(reduce_drive_graph(response)["edges"], [0, 1, 1112, 100, 1, 0, 1112, 100])

    def test_a_closed_way_on_its_own_adds_no_loop(self) -> None:
        response = {"elements": [_road({"highway": "service"}, [1, 2, 3, 1])]}
        self.assertEqual(reduce_drive_graph(response), {"nodes": [], "edges": []})


def _node(node_id: int, tags: dict, lat: float, lon: float) -> dict:
    return {"type": "node", "id": node_id, "lat": lat, "lon": lon, "tags": tags}


def _box(lat: float, lon: float, dlat: float, dlon: float) -> list[Point]:
    return [(lat, lon), (lat, lon + dlon), (lat + dlat, lon + dlon), (lat + dlat, lon), (lat, lon)]


def _pois(tags: dict) -> list[list]:
    return reduce_places({"elements": [_node(1, tags, 56.9, 24.1)]})["pois"]


def _kinds(tags: dict, ring: list[Point]) -> list[str]:
    return [area[0] for area in reduce_places({"elements": [_way(1, tags, ring)]})["areas"]]


BIG = _box(56.90, 24.10, 0.01, 0.02)
SMALL_RING = [(56.70, 24.10), (56.70, 24.101), (56.701, 24.101), (56.701, 24.10), (56.70, 24.10)]


class ReducePlacesTests(TestCase):
    def test_shops_and_gyms_become_points_named_by_brand_or_name(self) -> None:
        mall = {
            "type": "relation",
            "id": 8,
            "tags": {"shop": "mall", "name": "Origo"},
            "members": [
                _member("outer", _box(56.94, 24.12, 0.002, 0.002)),
                _member("outer", _box(56.946, 24.13, 0.002, 0.002)),
            ],
        }
        response = {
            "elements": [
                _node(1, {"shop": "supermarket", "brand": "Rimi", "name": "Rimi Teika"}, 56.9500004, 24.15),
                _node(2, {"shop": "grocery"}, 56.951, 24.151),
                _way(3, {"shop": "convenience", "name": "Aibe"}, _box(56.96, 24.10, 0.002, 0.004)),
                _node(4, {"leisure": "fitness_centre", "name": "MyFitness"}, 56.97, 24.1),
                _node(5, {"leisure": "sports_centre", "sport": "swimming;fitness"}, 56.98, 24.1),
                _node(6, {"shop": "doityourself", "brand": "Depo"}, 56.99, 24.1),
                _node(7, {"shop": "trade", "name": "Kurši"}, 56.91, 24.1),
                mall,
                _node(9, {"shop": "bakery", "name": "Lāči"}, 56.92, 24.1),
            ]
        }
        self.assertEqual(
            reduce_places(response),
            {
                "pois": [
                    ["grocery", "Rimi", 56.95, 24.15],
                    ["grocery", "", 56.951, 24.151],
                    ["grocery", "Aibe", 56.961, 24.102],
                    ["gym", "MyFitness", 56.97, 24.1],
                    ["gym", "", 56.98, 24.1],
                    ["diy", "Depo", 56.99, 24.1],
                    ["diy", "Kurši", 56.91, 24.1],
                    ["mall", "Origo", 56.944, 24.126],
                ],
                "areas": [],
            },
        )

    def test_fuel_station_shops_and_kiosks_are_not_groceries(self) -> None:
        self.assertEqual(_pois({"shop": "convenience", "name": "Aibe"}), [["grocery", "Aibe", 56.9, 24.1]])
        self.assertEqual(_pois({"shop": "supermarket", "name": "Nesteļa veikals"}), [["grocery", "Nesteļa veikals", 56.9, 24.1]])
        for tags in (
            {"amenity": "fuel", "name": "Degvielas stacija"},
            {"brand": "Circle K"},
            {"name": "NESTE"},
            {"brand": "Virši"},
            {"name": "viada"},
            {"brand": "Gotika"},
            {"brand": "Narvesen", "name": "Kiosks"},
            {"name": "Plus Punkts"},
            {"name": "Gotika auto"},
            {"brand": "Virši-A"},
        ):
            with self.subTest(tags):
                self.assertEqual(_pois({"shop": "convenience", **tags}), [])

    def test_outdoor_workout_spots_and_other_sports_are_not_gyms(self) -> None:
        for tags in (
            {"leisure": "fitness_station", "sport": "fitness"},
            {"leisure": "pitch", "sport": "fitness"},
            {"leisure": "sports_centre", "sport": "swimming"},
            {"leisure": "sports_centre"},
            {"sport": "fitness"},
        ):
            with self.subTest(tags):
                self.assertEqual(_pois(tags), [])

    def test_each_kind_of_area_is_recognised(self) -> None:
        cases = [
            ({"landuse": "industrial"}, ["industrial"]),
            ({"man_made": "works"}, ["works"]),
            ({"landuse": "landfill"}, ["landfill"]),
            ({"man_made": "wastewater_plant"}, ["wastewater"]),
            ({"landuse": "cemetery"}, ["cemetery"]),
            ({"amenity": "grave_yard"}, ["cemetery"]),
            ({"landuse": "cemetery", "amenity": "grave_yard"}, ["cemetery"]),
            ({"natural": "wetland"}, ["bog"]),
            ({"natural": "wetland", "wetland": "bog"}, ["bog"]),
            ({"natural": "wetland", "wetland": "swamp"}, ["bog"]),
            ({"natural": "wetland", "wetland": "reedbed"}, []),
            ({"natural": "wetland", "wetland": "tidalflat"}, []),
            ({"natural": "wetland", "wetland": "saltmarsh"}, []),
            ({"landuse": "railway"}, ["railway"]),
            ({"landuse": "industrial", "man_made": "works"}, ["industrial", "works"]),
            ({"landuse": "residential"}, []),
        ]
        for tags, kinds in cases:
            with self.subTest(tags):
                self.assertEqual(_kinds(tags, BIG), kinds)

    def test_an_area_is_its_outline_with_its_size_in_hectares(self) -> None:
        response = {"elements": [_way(1, {"landuse": "cemetery", "name": "Meža kapi"}, BIG)]}
        [area] = reduce_places(response)["areas"]
        self.assertEqual(area[:2], ["cemetery", "Meža kapi"])
        self.assertEqual(decode_polyline(area[2]), BIG)
        self.assertEqual(area[3], 135.04)

    def test_the_size_does_not_depend_on_the_winding(self) -> None:
        response = {"elements": [_way(1, {"landuse": "cemetery"}, BIG[::-1])]}
        self.assertEqual(reduce_places(response)["areas"][0][3], 135.04)

    def test_an_unclosed_outline_is_no_area(self) -> None:
        self.assertEqual(_kinds({"landuse": "cemetery"}, BIG[:-1]), [])

    def test_industrial_land_under_two_hectares_is_dropped_but_works_on_it_are_kept(self) -> None:
        small = _box(56.9, 24.1, 0.001, 0.002)
        self.assertEqual(_kinds({"landuse": "industrial", "man_made": "works"}, small), ["works"])
        self.assertEqual(_kinds({"landuse": "industrial"}, _box(56.9, 24.1, 0.0015, 0.002)), ["industrial"])

    def test_outlines_are_simplified_to_ten_metres(self) -> None:
        ring = [
            (56.90, 24.10), (56.90008, 24.105), (56.90, 24.11), (56.91, 24.11),
            (56.9102, 24.105), (56.91, 24.10), (56.90, 24.10),
        ]
        [area] = reduce_places({"elements": [_way(1, {"landuse": "landfill"}, ring)]})["areas"]
        self.assertEqual(decode_polyline(area[2]), [ring[0], *ring[2:]])

    def test_a_relation_contributes_its_assembled_outer_rings(self) -> None:
        relation = {
            "type": "relation",
            "id": 9,
            "tags": {"natural": "wetland", "wetland": "bog", "name": "Medema purvs"},
            "members": [
                _member("outer", [(56.80, 24.10), (56.80, 24.12), (56.81, 24.12)]),
                _member("outer", [(56.81, 24.12), (56.81, 24.10), (56.80, 24.10)]),
                _member("inner", _box(56.803, 24.103, 0.002, 0.002)),
                _member("outer", SMALL_RING),
                _member("outer", [(56.60, 24.10), (56.60, 24.12)]),
            ],
        }
        areas = reduce_places({"elements": [relation]})["areas"]
        self.assertEqual([area[:2] for area in areas], [["bog", "Medema purvs"]] * 2)
        rings = sorted(decode_polyline(area[2]) for area in areas)
        self.assertEqual(rings[0], SMALL_RING)
        self.assertEqual(sorted(rings[1][:-1]), [(56.80, 24.10), (56.80, 24.12), (56.81, 24.10), (56.81, 24.12)])
        self.assertEqual(rings[1][0], rings[1][-1])

    def test_a_works_mapped_as_a_point_is_a_one_point_ring_of_no_size(self) -> None:
        response = {"elements": [_node(1, {"man_made": "works", "name": "Aldaris"}, 56.97, 24.12)]}
        self.assertEqual(reduce_places(response)["areas"], [["works", "Aldaris", encode_polyline([(56.97, 24.12)]), 0]])

    def test_the_places_query_covers_riga_and_asks_for_geometry(self) -> None:
        self.assertIn("out geom", PLACES_QUERY)
        self.assertIn("(56.85,23.9,57.1,24.35)", PLACES_QUERY)
