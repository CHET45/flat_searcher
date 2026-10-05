from dataclasses import replace
from unittest import TestCase

from flat_searcher.shortlist.criteria import TodayRules, parse_criteria
from flat_searcher.shortlist.rank import evaluate, total_expected_minutes
from flat_searcher.shortlist.today import pick_today

CRITERIA = parse_criteria("[price]\nmax_eur = 100000\n")
TARGETS = ["office", "campus"]
RULES = TodayRules()


def _option(minutes: int, every: int = 10) -> dict:
    return {"minutes": minutes, "every_min": every, "transfers": 0, "walk_m": 300, "legs": []}


def _flat(ss_id: str, price: int = 35000, minutes: int = 25, area: float = 50, floor: int = 3,
          description: str = "", ratio: float = 1.0, campus: list | None = None, building: dict | None = None,
          grocery: int | None = None):
    record = {
        "ss_id": ss_id,
        "status": "active",
        "core": {"price_eur": price, "area_m2": area, "floor": floor, "declared_rooms": 2},
        "text": {"description": description},
        "market": {"price_per_m2": 1000 * ratio, "district_median_price_per_m2": 1000},
    }
    transit = {"targets": {"office": [_option(minutes)],
                           "campus": [_option(minutes)] if campus is None else campus}}
    if building is not None:
        transit["building"] = building
    if grocery is not None:
        transit["surroundings"] = {"walk": {"grocery": [{"name": "shop", "min": grocery}]}}
    return evaluate(record, CRITERIA, transit)


def _picks(flats, rules: TodayRules = RULES) -> list[tuple[str, int]]:
    return [(pick.ss_id, pick.front) for pick in pick_today(flats, rules, TARGETS)]


class TodayTests(TestCase):
    def test_expected_minutes_add_half_the_headway_per_target(self) -> None:
        flat = _flat("a", minutes=20, campus=[_option(30, every=20), _option(35, every=4)])
        self.assertEqual(total_expected_minutes(flat.journeys, TARGETS), 25 + 37)
        self.assertIsNone(total_expected_minutes(_flat("b", campus=[]).journeys, TARGETS))

    def test_soft_rules_leave_out_flats_excluded_from_today(self) -> None:
        flats = [
            _flat("ok"),
            _flat("walkthrough", description="Divas caurstaigājamas istabas."),
            _flat("leased", description="Zeme zem mājas ir nomā."),
            _flat("replanned", description="Pārplānojums nav saskaņots."),
            _flat("implausible", ratio=0.3),
            _flat("room", area=18),
            _flat("no-way", campus=[]),
        ]
        self.assertEqual(_picks(flats), [("ok", 1)])
        lenient = TodayRules(exclude_walkthrough=False, exclude_leased_land=False, exclude_illegal_replanning=False)
        self.assertEqual(
            [ss_id for ss_id, _ in _picks(flats, lenient)], ["ok", "walkthrough", "leased", "replanned"]
        )

    def test_a_journey_cap_applies_to_the_sum_over_targets(self) -> None:
        flats = [_flat("near", minutes=20), _flat("far", minutes=40)]
        self.assertEqual(_picks(flats, replace(RULES, max_minutes=60)), [("near", 1)])

    def test_flats_beaten_on_price_time_and_area_at_once_come_in_a_later_front(self) -> None:
        flats = [
            _flat("beaten", price=36000, minutes=30, area=45),
            _flat("cheap", price=30000, minutes=30, area=45),
            _flat("fast-big", price=40000, minutes=20, area=60),
            _flat("beaten-twice", price=41000, minutes=31, area=44),
        ]
        self.assertEqual(
            _picks(flats), [("cheap", 1), ("fast-big", 1), ("beaten", 2), ("beaten-twice", 3)]
        )

    def test_the_view_stops_at_its_size_in_the_suggested_order(self) -> None:
        flats = [_flat("a", price=30000, area=40), _flat("b", price=40000, area=60), _flat("c", price=50000)]
        self.assertEqual(_picks(flats, replace(RULES, size=2)), [("a", 1), ("b", 1)])

    def test_building_wear_is_a_fourth_dimension_and_a_better_house_beats_a_worse_one(self) -> None:
        flats = [
            _flat("worn", building={"wear": "V4"}),
            _flat("sound", building={"wear": "V2"}),
            _flat("new", building={"note": "new build"}),
            _flat("unknown"),
        ]
        self.assertEqual(_picks(flats), [("new", 1), ("sound", 2), ("unknown", 3), ("worn", 4)])
        cheaper_but_worn = [_flat("worn", price=30000, building={"wear": "V4"}), _flat("sound", building={"wear": "V2"})]
        self.assertEqual(_picks(cheaper_but_worn), [("worn", 1), ("sound", 1)])

    def test_a_grocery_close_on_foot_beats_a_far_one_and_none_counts_as_far(self) -> None:
        flats = [_flat("far", grocery=25), _flat("near", grocery=4), _flat("none")]
        self.assertEqual(_picks(flats), [("near", 1), ("far", 2), ("none", 3)])
