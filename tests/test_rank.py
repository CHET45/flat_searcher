from unittest import TestCase

from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.shortlist.rank import evaluate, sort_key

CRITERIA = parse_criteria(
    """
[price]
max_eur = 60000
bands = [[30000, 40000], [0, 30000], [40000, 50000], [50000, 60000]]
[rooms]
order = [2, 1]
[building]
excluded_types = ["Koka"]
[heating]
exclude_stove = true
[floor]
exclude = [1]
[surroundings]
gym_max_min = 15
"""
)


def _record(
    ss_id: str,
    price: int | None = 35000,
    rooms: int | None = 2,
    building_type: str = "Paneļu",
    floor: int | None = 3,
    description: str = "Gaišs dzīvoklis.",
    series: str = "P. kara",
    ratio: float | None = 1.0,
) -> dict:
    return {
        "ss_id": ss_id,
        "status": "active",
        "core": {
            "price_eur": price,
            "declared_rooms": rooms,
            "floor": floor,
            "building_type": building_type,
            "building_series": series,
        },
        "fields": {"Sērija": series},
        "text": {"title": "", "description": description},
        "market": {
            "price_per_m2": None if ratio is None else 1000 * ratio,
            "district_median_price_per_m2": 1000 if ratio is not None else "unknown",
        },
    }


def _gyms(walk: int | None = None, ride: tuple[int, int] | None = None, reached: int = 1) -> dict:
    transit = _transit(*"abc"[:reached])
    transit["surroundings"] = {
        "walk": {"gym": [] if walk is None else [{"name": "g", "min": walk}]},
        "transit": {"gym": [] if ride is None else [{"name": "r", "min": ride[0], "every_min": ride[1]}]},
    }
    return transit


def _journey(*routes: str, minutes: int = 20, every: int = 10) -> dict:
    return {
        "minutes": minutes, "every_min": every, "transfers": len(routes) - 1, "walk_m": 300,
        "legs": [{"routes": [route], "board": "s1", "alight": "s2", "every_min": every,
                  "shape": None, "span": None} for route in routes],
    }


def _transit(*reached: str, via: str | None = None) -> dict:
    targets = {name: [_journey("tram 1")] if name in reached else [] for name in ("a", "b", "c")}
    if via:
        targets[via] = [_journey("tram 2", "bus 3")]
    return {"precision": "exact", "targets": targets}


def _order(*pairs: tuple[dict, dict | None]) -> list[str]:
    evaluations = [evaluate(record, CRITERIA, transit) for record, transit in pairs]
    return [evaluation.ss_id for evaluation in sorted(evaluations, key=sort_key)]


class GateTests(TestCase):
    def assertRejected(self, record: dict, reason: str | None) -> None:
        self.assertEqual(evaluate(record, CRITERIA, None).rejected, reason)

    def test_each_gate(self) -> None:
        self.assertRejected(_record("ok"), None)
        self.assertRejected(_record("p", price=60001), "price")
        self.assertRejected(_record("np", price=None), "price")
        self.assertRejected(_record("r", rooms=3), "rooms")
        self.assertRejected(_record("w", building_type="Koka"), "building_type")
        self.assertRejected(_record("s", description="Krāsns apkure."), "stove")
        self.assertRejected(_record("d", description="Dzīvoklis pārdodas kā domājamā daļa."), "share")

    def test_excluded_floors_are_rejected_and_an_unknown_floor_passes(self) -> None:
        self.assertRejected(_record("ground", floor=1), "floor")
        self.assertRejected(_record("second", floor=2), None)
        self.assertRejected(_record("unknown", floor=None), None)

    def test_no_gym_nearby_is_not_a_rejection(self) -> None:
        self.assertRejected(_record("far"), None)
        self.assertEqual(evaluate(_record("far"), CRITERIA, _gyms(walk=40)).rejected, None)
        self.assertEqual(evaluate(_record("none"), CRITERIA, _gyms()).rejected, None)

    def test_first_failing_gate_is_the_reason(self) -> None:
        self.assertRejected(_record("x", price=90000, rooms=4, building_type="Koka"), "price")

    def test_missing_rooms_pass(self) -> None:
        self.assertRejected(_record("m", rooms=None), None)

    def test_stove_mention_is_not_a_rejection(self) -> None:
        self.assertRejected(_record("m", description="Saglabāta podiņu krāsns."), None)


class OrderTests(TestCase):
    def test_price_band_comes_before_rooms(self) -> None:
        ideal_one_room = _record("ideal-1", price=38000, rooms=1)
        dearer_two_room = _record("dear-2", price=42000, rooms=2)
        self.assertEqual(_order((dearer_two_room, None), (ideal_one_room, None)), ["ideal-1", "dear-2"])

    def test_preferred_band_order_is_not_price_order(self) -> None:
        cheap = _record("cheap", price=25000)
        ideal = _record("ideal", price=39000)
        self.assertEqual(_order((cheap, None), (ideal, None)), ["ideal", "cheap"])

    def test_rooms_come_before_layout(self) -> None:
        two_walkthrough = _record("two", description="Divas caurstaigājamas istabas.")
        one_room = _record("one", rooms=1)
        missing = _record("missing", rooms=None)
        self.assertEqual(
            _order((missing, None), (one_room, None), (two_walkthrough, None)), ["two", "one", "missing"]
        )

    def test_layout_comes_before_transit(self) -> None:
        isolated = _record("isolated", description="2 izolētas istabas.")
        connected = _record("connected")
        self.assertEqual(
            _order((connected, _transit("a", "b", "c")), (isolated, None)), ["isolated", "connected"]
        )

    def test_more_targets_reached_sorts_first(self) -> None:
        self.assertEqual(
            _order((_record("one"), _transit("a")), (_record("three"), _transit("a", "b", "c"))),
            ["three", "one"],
        )

    def test_a_flat_without_a_gym_within_reach_on_foot_or_by_transit_sorts_after_the_others(self) -> None:
        self.assertEqual(
            _order(
                (_record("a-far"), _gyms(walk=16, ride=(12, 10))),
                (_record("b-none"), _gyms()),
                (_record("c-near"), _gyms(walk=15)),
                (_record("d-ride"), _gyms(ride=(10, 8))),
                (_record("e-unknown"), _transit("a")),
            ),
            ["c-near", "d-ride", "e-unknown", "a-far", "b-none"],
        )

    def test_targets_reached_count_before_the_gym(self) -> None:
        self.assertEqual(
            _order((_record("near"), _gyms(walk=5)), (_record("reached"), _gyms(reached=3))),
            ["reached", "near"],
        )

    def test_cheaper_relative_to_the_district_sorts_first_and_unknown_last(self) -> None:
        self.assertEqual(
            _order(
                (_record("unknown", ratio=None), None),
                (_record("dear", ratio=1.2), None),
                (_record("cheap", ratio=0.8), None),
            ),
            ["cheap", "dear", "unknown"],
        )

    def test_a_target_reached_only_with_a_transfer_is_not_reached_directly(self) -> None:
        self.assertEqual(
            _order((_record("via"), _transit("a", via="b")), (_record("direct"), _transit("a", "b"))),
            ["direct", "via"],
        )
        self.assertEqual(evaluate(_record("via"), CRITERIA, _transit("a", via="b")).reached, 1)

    def test_evaluation_exposes_what_the_digest_shows(self) -> None:
        evaluation = evaluate(_record("e", ratio=0.8), CRITERIA, _transit("a"))
        self.assertEqual(evaluation.band, 0)
        self.assertAlmostEqual(evaluation.price_ratio or 0, 0.8)
        self.assertEqual(evaluation.reached, 1)
        self.assertEqual(evaluation.precision, "exact")
        self.assertEqual(evaluation.journeys["a"], [_journey("tram 1")])
