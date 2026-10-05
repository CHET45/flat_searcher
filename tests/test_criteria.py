from unittest import TestCase

from flat_searcher.shortlist.criteria import (
    CriteriaError,
    Target,
    TodayRules,
    changed_targets,
    parse_criteria,
    target_definition,
)

SKETCH = """
[price]
max_eur = 60000
bands = [[30000, 40000], [0, 30000], [40000, 50000], [50000, 60000]]

[rooms]
order = [2, 1]

[building]
excluded_types = ["Koka"]

[heating]
exclude_stove = true

[transit]
walk_m = 400
transfer_walk_m = 250
window_start = "06:30"
window_end = "09:30"
max_transfers = 1
journeys_max = 3

[today]
size = 12
max_minutes = 110
exclude_walkthrough = false
exclude_leased_land = false

[floor]
exclude = [1, 0]

[[transit.targets]]
name = "office"
address = "Example iela 1"

[[transit.targets]]
name = "school"
address = "Other iela 2"
lat = 56.95
lon = 24.1
"""


class ParseCriteriaTests(TestCase):
    def test_sketch_parses_into_typed_values(self) -> None:
        criteria = parse_criteria(SKETCH)
        self.assertEqual(criteria.max_eur, 60000)
        self.assertEqual(criteria.room_order, (2, 1))
        self.assertEqual(criteria.excluded_types, frozenset({"Koka"}))
        self.assertTrue(criteria.exclude_stove)
        self.assertEqual(criteria.walk_m, 400)
        self.assertEqual(criteria.transfer_walk_m, 250)
        self.assertEqual(criteria.window, ("06:30", "09:30"))
        self.assertEqual((criteria.max_transfers, criteria.journeys_max), (1, 3))
        self.assertEqual(
            criteria.targets,
            (
                Target("office", "Example iela 1", None, None),
                Target("school", "Other iela 2", 56.95, 24.1),
            ),
        )
        self.assertEqual(criteria.today, TodayRules(12, 110, False, False))
        self.assertEqual(criteria.excluded_floors, frozenset({0, 1}))

    def test_band_index_follows_preference_order_and_half_open_bounds(self) -> None:
        criteria = parse_criteria(SKETCH)
        self.assertEqual(criteria.band_index(35000), 0)
        self.assertEqual(criteria.band_index(29999), 1)
        self.assertEqual(criteria.band_index(40000), 2)
        self.assertEqual(criteria.band_index(60000), 3)
        self.assertIsNone(criteria.band_index(60001))

    def test_missing_sections_take_neutral_defaults(self) -> None:
        criteria = parse_criteria("[price]\nmax_eur = 50000\n")
        self.assertEqual(criteria.bands, ((0, 50000),))
        self.assertEqual(criteria.band_index(50000), 0)
        self.assertEqual(criteria.room_order, ())
        self.assertEqual(criteria.excluded_types, frozenset())
        self.assertFalse(criteria.exclude_stove)
        self.assertEqual(criteria.walk_m, 500)
        self.assertEqual(criteria.transfer_walk_m, 300)
        self.assertEqual(criteria.window, ("07:00", "10:00"))
        self.assertEqual((criteria.max_transfers, criteria.journeys_max), (2, 4))
        self.assertEqual(criteria.targets, ())
        self.assertEqual(criteria.today, TodayRules(20, None, True, True))
        self.assertEqual(criteria.excluded_floors, frozenset())

    def test_unknown_keys_are_errors_that_name_the_key(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "max_price"):
            parse_criteria("[price]\nmax_price = 1\n")
        with self.assertRaisesRegex(CriteriaError, "colour"):
            parse_criteria("[price]\nmax_eur = 1\n[colour]\nx = 1\n")
        with self.assertRaisesRegex(CriteriaError, "walk"):
            parse_criteria('[price]\nmax_eur = 1\n[[transit.targets]]\nname = "a"\naddress = "b"\nwalk = 1\n')

    def test_the_ground_floor_is_a_gate_not_a_today_rule(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "today.exclude_ground_floor"):
            parse_criteria("[price]\nmax_eur = 1\n[today]\nexclude_ground_floor = true\n")

    def test_excluded_floors_are_integers(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "floor.exclude"):
            parse_criteria('[price]\nmax_eur = 1\n[floor]\nexclude = ["1"]\n')
        with self.assertRaisesRegex(CriteriaError, "floor.exclude"):
            parse_criteria("[price]\nmax_eur = 1\n[floor]\nexclude = 1\n")

    def test_price_cap_is_required(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "max_eur"):
            parse_criteria("[rooms]\norder = [1]\n")

    def test_target_needs_an_address_or_both_coordinates(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "office"):
            parse_criteria('[price]\nmax_eur = 1\n[[transit.targets]]\nname = "office"\nlat = 56.9\n')

    def test_malformed_toml_is_a_criteria_error(self) -> None:
        with self.assertRaises(CriteriaError):
            parse_criteria("[price\n")

    def test_band_must_be_an_ordered_pair(self) -> None:
        with self.assertRaisesRegex(CriteriaError, "bands"):
            parse_criteria("[price]\nmax_eur = 9\nbands = [[5, 1]]\n")


class ChangedTargetsTests(TestCase):
    def test_a_target_is_current_only_when_transit_computed_its_definition(self) -> None:
        criteria = parse_criteria(SKETCH)
        office, school = criteria.targets
        computed = {
            "office": {"lat": 56.9, "lon": 24.1, "defined_as": target_definition(office)},
            "school": {"lat": 56.95, "lon": 24.1, "defined_as": target_definition(school)},
        }
        self.assertEqual(changed_targets(criteria, computed), [])
        self.assertEqual(changed_targets(criteria, {"office": computed["office"]}), ["school"])
        moved = {**computed, "office": {**computed["office"],
                                        "defined_as": {**target_definition(office), "address": "Old iela 9"}}}
        self.assertEqual(changed_targets(criteria, moved), ["office"])
        renamed = {"old office": computed["office"], "school": computed["school"]}
        self.assertEqual(changed_targets(criteria, renamed), ["office"])

    def test_points_written_before_definitions_were_kept_are_matched_by_name(self) -> None:
        criteria = parse_criteria(SKETCH)
        computed = {"office": {"lat": 56.9, "lon": 24.1}, "school": {"lat": 56.95, "lon": 24.1}}
        self.assertEqual(changed_targets(criteria, computed), [])
