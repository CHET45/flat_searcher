from datetime import date
from unittest import TestCase

from flat_searcher.transit.gtfs import TransitFeed
from journeys_fixture import FLAT, build_feed, point


class TransitFeedTests(TestCase):
    def setUp(self) -> None:
        self.feed = TransitFeed.from_zip(build_feed(), date(2026, 9, 26), transfer_walk_m=300)

    def test_reference_day_is_the_next_weekday_without_a_timetable_exception(self) -> None:
        self.assertEqual(self.feed.day, date(2026, 9, 29))

    def test_only_services_of_the_reference_day_are_kept(self) -> None:
        labels = {pattern.label for pattern in self.feed.patterns}
        self.assertEqual(labels, {"bus 1", "bus 5", "bus 8", "tram 2", "bus 3", "bus 9"})

    def test_trips_group_by_route_direction_and_stop_sequence(self) -> None:
        bus_one = [pattern for pattern in self.feed.patterns if pattern.label == "bus 1"]
        self.assertEqual({pattern.stops for pattern in bus_one}, {("h1", "m1", "t1"), ("t1", "m1", "h1")})
        outbound = next(pattern for pattern in bus_one if pattern.stops[0] == "h1")
        self.assertEqual(len(outbound.trips), 96)
        self.assertEqual(outbound.trips[0].departures, (6 * 3600, 6 * 3600 + 300, 6 * 3600 + 600))
        self.assertEqual(outbound.trips[1].arrivals[0], 6 * 3600 + 600)

    def test_stops_carry_their_shape_index_in_order(self) -> None:
        outbound = next(p for p in self.feed.patterns if p.label == "bus 1" and p.stops[0] == "h1")
        self.assertEqual(outbound.shape, "bus_1_shape")
        self.assertEqual(outbound.shape_indices, (0, 4, 8))
        self.assertEqual(len(self.feed.shapes["bus_1_shape"]), 9)
        unshaped = next(p for p in self.feed.patterns if p.label == "bus 9")
        self.assertIsNone(unshaped.shape)

    def test_footpaths_join_stops_within_the_transfer_walk(self) -> None:
        self.assertEqual([stop for stop, _ in self.feed.footpaths["hub_a"]], ["hub_b"])
        self.assertAlmostEqual(self.feed.footpaths["hub_a"][0][1], 2.34, places=2)
        self.assertEqual([stop for stop, _ in self.feed.footpaths["h1"]], ["h8"])
        self.assertEqual(self.feed.footpaths.get("x1", []), [])

    def test_stops_near_report_their_distance(self) -> None:
        near = self.feed.stops_near(*point(*FLAT), 500)
        self.assertEqual(list(near), ["h1", "h8"])
        self.assertAlmostEqual(near["h1"], 100, delta=1)
        self.assertEqual(self.feed.stop_names["h1"], "Home stop")

    def test_label_names_the_service_period(self) -> None:
        self.assertEqual(self.feed.label, "20260901-20270901")
