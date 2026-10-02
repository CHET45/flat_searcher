from datetime import date
from unittest import TestCase

from flat_searcher.transit.gtfs import TransitFeed
from flat_searcher.transit.journeys import Planner
from flat_searcher.transit.walking import WalkGraph
from journeys_fixture import FLAT, TARGET_ONE, TARGET_TWO, build_feed, point

FEED = TransitFeed.from_zip(build_feed(), date(2026, 9, 26), transfer_walk_m=300)


def _planner(max_transfers: int = 2) -> Planner:
    return Planner(FEED, window=("07:00", "10:00"), walk_m=500, max_transfers=max_transfers,
                   journeys_max=4)


class PlannerTests(TestCase):
    def test_buses_between_the_same_stops_form_one_option_with_a_combined_headway(self) -> None:
        planner = _planner()
        options = planner.options(planner.target(*point(*TARGET_ONE)), *point(*FLAT))
        self.assertEqual([[leg.routes for leg in option.legs] for option in options],
                         [[("bus 1", "bus 5")], [("bus 8",)]])
        option = options[0]
        self.assertEqual((option.minutes, option.every_min, option.transfers, option.walk_m), (13, 8, 0, 260))
        leg = option.legs[0]
        self.assertEqual((leg.board, leg.alight), ("h1", "t1"))
        self.assertEqual((leg.every_min, leg.shape, leg.span), (8, "bus_1_shape", (0, 8)))

    def test_options_are_ordered_by_the_expected_time_including_half_the_headway(self) -> None:
        planner = _planner()
        options = planner.options(planner.target(*point(*TARGET_ONE)), *point(*FLAT))
        rare = options[1]
        self.assertEqual((rare.minutes, rare.every_min), (10, 60))
        self.assertGreater(options[0].minutes, rare.minutes)

    def test_transfer_option_is_paced_by_its_rarest_leg_and_waits_in_the_median(self) -> None:
        planner = _planner()
        options = planner.options(planner.target(*point(*TARGET_TWO)), *point(*FLAT))
        self.assertEqual([[leg.routes for leg in option.legs] for option in options], [[("tram 2",), ("bus 3",)]])
        option = options[0]
        self.assertEqual((option.minutes, option.every_min, option.transfers, option.walk_m), (33, 15, 1, 455))
        self.assertEqual([leg.every_min for leg in option.legs], [6, 15])
        self.assertEqual([(leg.board, leg.alight) for leg in option.legs], [("h1", "hub_a"), ("hub_b", "t2")])

    def test_a_route_running_only_outside_the_window_is_not_an_option(self) -> None:
        planner = _planner()
        options = planner.options(planner.target(*point(*TARGET_ONE)), *point(*FLAT))
        self.assertNotIn("bus 9", [route for option in options for leg in option.legs for route in leg.routes])

    def test_transfers_can_be_forbidden(self) -> None:
        planner = _planner(max_transfers=0)
        self.assertEqual(planner.options(planner.target(*point(*TARGET_TWO)), *point(*FLAT)), [])

    def test_a_flat_far_from_every_stop_has_no_options(self) -> None:
        planner = _planner()
        self.assertEqual(planner.options(planner.target(*point(*TARGET_ONE)), 57.5, 25.0), [])

    def test_options_serialise_by_stop_id(self) -> None:
        planner = _planner()
        option = planner.options(planner.target(*point(*TARGET_TWO)), *point(*FLAT))[0]
        data = option.as_dict()
        self.assertEqual(data["legs"][1], {
            "routes": ["bus 3"], "board": "hub_b", "alight": "t2", "every_min": 15,
            "shape": "bus_3_shape", "span": [0, 1],
        })
        self.assertEqual({key: data[key] for key in ("minutes", "every_min", "transfers", "walk_m")},
                         {"minutes": 33, "every_min": 15, "transfers": 1, "walk_m": 455})


class PlannerWithStreetsTests(TestCase):
    """Streets: an L-shaped path from the flat to its stop, a straight one from the target's stop."""

    GRAPH = WalkGraph.from_data({
        "nodes": [*point(0, 100), *point(60, 100), *point(60, 0), *point(0, 0), *point(4000, 0), *point(4000, 100)],
        "edges": [0, 1, 1, 2, 2, 3, 4, 5],
    })

    def _planner(self) -> Planner:
        return Planner(FEED, window=("07:00", "10:00"), walk_m=500, max_transfers=2, journeys_max=4,
                       walk=self.GRAPH)

    def test_walks_follow_the_streets_and_time_the_real_path(self) -> None:
        planner = self._planner()
        option = planner.options(planner.target(*point(*TARGET_ONE)), *point(*FLAT))[0]
        self.assertEqual([leg.routes for leg in option.legs], [("bus 1", "bus 5")])
        self.assertEqual((option.minutes, option.walk_m), (14, 320))
        self.assertEqual([walk.m for walk in option.walks], [220, 100])
        self.assertTrue(all(walk.on_streets for walk in option.walks))
        self.assertEqual(len(option.walks[0].points), 5)
        self.assertEqual(option.as_dict()["walks"][1], {"m": 100, "line": option.walks[1].encoded})

    def test_a_transfer_far_from_any_street_keeps_the_straight_line_fallback(self) -> None:
        planner = self._planner()
        option = planner.options(planner.target(*point(*TARGET_TWO)), *point(*FLAT))[0]
        self.assertEqual([leg.routes for leg in option.legs], [("tram 2",), ("bus 3",)])
        self.assertEqual([walk.on_streets for walk in option.walks], [True, False, False])
        self.assertEqual(option.walks[1].m, 195)
        self.assertEqual(option.walk_m, 220 + 195 + 130)
