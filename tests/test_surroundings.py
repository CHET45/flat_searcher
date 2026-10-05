from unittest import TestCase

from flat_searcher.transit.driving import DriveGraph
from flat_searcher.transit.geometry import encode_polyline
from flat_searcher.transit.surroundings import Places, surroundings, target_fields
from flat_searcher.transit.walking import WalkGraph

LAT = 56.95
STEP = 0.01


def _at(index: int, north: float = 0.0) -> tuple[float, float]:
    return LAT + north, 24.10 + index * STEP


ROAD = [_at(index) for index in range(5)] + [_at(2, 0.01)]
DRIVE = DriveGraph.from_data({
    "nodes": [value for point in ROAD for value in point],
    "edges": [*(value for a in range(4) for value in (a, a + 1, 608, 60)),
              *(value for a in range(4) for value in (a + 1, a, 608, 60)),
              2, 5, 1112, 300, 5, 2, 1112, 300],
})
WALK_NODES = [(LAT, 24.10 + index * 0.001) for index in range(30)]
WALK = WalkGraph.from_data({
    "nodes": [value for point in WALK_NODES for value in point],
    "edges": [value for a in range(29) for value in (a, a + 1)],
})
FLAT = ROAD[0]
TARGET = ROAD[4]


def _ring(lat: float, lon: float, half: float) -> str:
    corners = [(lat - half, lon - half), (lat - half, lon + half), (lat + half, lon + half),
               (lat + half, lon - half), (lat - half, lon - half)]
    return encode_polyline(corners)


PLACES = Places({
    "pois": [
        ["grocery", "On route", *ROAD[2]],
        ["grocery", "Corner", *WALK_NODES[3]],
        ["grocery", "Spur", *ROAD[5]],
        ["diy", "Spur DIY", *ROAD[5]],
        ["gym", "Far gym", LAT + 0.05, 24.10],
    ],
    "areas": [
        ["cemetery", "Kapi", _ring(LAT, 24.103, 0.001), 1.0],
        ["industrial", "", _ring(LAT + 0.004, 24.10, 0.001), 3.0],
        ["landfill", "Far", _ring(LAT + 0.2, 24.10, 0.01), 50.0],
        ["park", "Not a nuisance", _ring(LAT, 24.10, 0.001), 1.0],
    ],
})


class PlacesTests(TestCase):
    def test_near_keeps_one_category_within_the_radius(self) -> None:
        self.assertEqual([place.name for place in PLACES.near("grocery", *FLAT, 500)], ["Corner"])
        self.assertEqual(sorted(place.name for place in PLACES.near("grocery", *FLAT, 2000)),
                         ["Corner", "On route", "Spur"])
        self.assertEqual(PLACES.near("gym", *FLAT, 2000), [])

    def test_around_measures_to_the_nearest_edge_or_zero_inside_and_hides_what_is_far(self) -> None:
        around = PLACES.around(*FLAT)
        self.assertEqual(list(around), ["cemetery", "industrial"])
        self.assertAlmostEqual(around["cemetery"]["m"], 2 * 0.001 * 111_195 * 0.545, delta=5)
        self.assertAlmostEqual(around["industrial"]["m"], 0.003 * 111_195, delta=5)
        self.assertEqual(PLACES.around(LAT, 24.103)["cemetery"]["m"], 0)


class SurroundingsTests(TestCase):
    def _found(self, first_walk: str | None = None) -> dict:
        fields = {"office": target_fields(DRIVE, *TARGET)}
        return surroundings(FLAT, WALK, DRIVE, PLACES, fields, {"office": first_walk} if first_walk else {})

    def test_nearest_on_foot_and_by_car(self) -> None:
        found = self._found()
        self.assertEqual([(p["name"], p["min"]) for p in found["walk"]["grocery"]], [("Corner", 2), ("On route", 15)])
        self.assertEqual(found["walk"]["gym"], [])
        self.assertEqual([(p["name"], p["min"]) for p in found["drive"]["grocery"]],
                         [("Corner", 1), ("On route", 2), ("Spur", 7)])
        self.assertEqual(found["drive"]["targets"]["office"], {"min": 4, "km": 2.4, "back_min": 4, "back_km": 2.4})

    def test_the_nearest_gym_by_car(self) -> None:
        places = Places({"pois": [["gym", "Spur gym", *ROAD[5]], ["gym", "Road gym", *ROAD[2]]], "areas": []})
        found = surroundings(FLAT, WALK, DRIVE, places, {}, {})
        self.assertEqual([(p["name"], p["min"], p["km"]) for p in found["drive"]["gym"]], [("Road gym", 2, 1.2)])

    def test_a_shop_on_the_route_is_on_the_way_and_one_up_a_spur_is_not(self) -> None:
        way = self._found()["on_the_way"]["office"]
        self.assertEqual(way["there"]["grocery"]["name"], "On route")
        self.assertEqual(way["there"]["grocery"]["plus_min"], 0)
        self.assertEqual(way["back"]["grocery"]["name"], "On route")
        self.assertNotIn("diy", way["there"])

    def test_at_an_equal_detour_a_named_shop_is_preferred(self) -> None:
        places = Places({"pois": [["grocery", "", *ROAD[2]], ["grocery", "Rimi", ROAD[3][0], ROAD[3][1] - 0.0005]],
                         "areas": []})
        found = surroundings(FLAT, WALK, DRIVE, places, {"office": target_fields(DRIVE, *TARGET)}, {})
        self.assertEqual(found["on_the_way"]["office"]["there"]["grocery"]["name"], "Rimi")

    def test_a_grocery_beside_the_walk_to_the_stop_is_on_the_way_on_foot(self) -> None:
        beside = encode_polyline([FLAT, (LAT + 0.0005, 24.104)])
        self.assertEqual(self._found(beside)["on_the_way"]["office"]["on_foot"]["grocery"]["name"], "Corner")
        away = encode_polyline([FLAT, (LAT + 0.01, 24.10)])
        self.assertNotIn("on_foot", self._found(away)["on_the_way"]["office"])
