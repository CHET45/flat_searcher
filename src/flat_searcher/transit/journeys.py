"""Journeys with transfers: RAPTOR backwards from each target, measured on the timetable.

One backward run per arrival deadline yields the latest departure from every stop in the
city, so three targets cover thousands of flats. Every journey found for a flat's stops is a
template; templates that use the same stops merge into one option whose legs list every
route between those stops, and each option is measured over the window on the timetable.
Walks follow the street graph when one is given, else the straight line with a road detour.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from flat_searcher.transit.geometry import (
    ROAD_DETOUR,
    WALK_SPEED_M_PER_MIN,
    Point,
    distance_m,
)
from flat_searcher.transit.gtfs import Pattern, TransitFeed
from flat_searcher.transit.walking import Walk, WalkField, WalkGraph

DEADLINE_STEP_S = 300
DEADLINE_SLACK_S = 90 * 60
MIN_TRANSFER_S = 60
DEFAULT_TRANSFER_WALK_M = 300
FIELD_REACH = 2

Parent = tuple[Any, ...] | None
Labels = list[dict[str, tuple[int, Parent]]]
LegTemplate = tuple[int, int, int]
StopPair = tuple[str, str]


@dataclass(frozen=True)
class Leg:
    routes: tuple[str, ...]
    board: str
    alight: str
    every_min: int
    shape: str | None
    span: tuple[int, int] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "routes": list(self.routes),
            "board": self.board,
            "alight": self.alight,
            "every_min": self.every_min,
            "shape": self.shape,
            "span": list(self.span) if self.span else None,
        }


@dataclass(frozen=True)
class Journey:
    minutes: int
    every_min: int
    transfers: int
    walk_m: int
    legs: tuple[Leg, ...]
    walks: tuple[Walk, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "minutes": self.minutes,
            "every_min": self.every_min,
            "transfers": self.transfers,
            "walk_m": self.walk_m,
            "legs": [leg.as_dict() for leg in self.legs],
            "walks": [walk.as_dict() for walk in self.walks],
        }


@dataclass
class Target:
    """Each stop near a target point leads on foot to the point it is the shortest walk from."""

    egress: dict[str, tuple[int, Walk]]
    runs: list[Labels]
    templates: dict[tuple[int, int, str], tuple[LegTemplate, ...]]

    def point_of(self, journey: Journey) -> int:
        return self.egress[journey.legs[-1].alight][0]


class Planner:
    def __init__(
        self,
        feed: TransitFeed,
        window: tuple[str, str],
        walk_m: float,
        max_transfers: int,
        journeys_max: int,
        walk: WalkGraph | None = None,
        transfer_walk_m: float = DEFAULT_TRANSFER_WALK_M,
    ) -> None:
        self._feed = feed
        self._window = (_clock_seconds(window[0]), _clock_seconds(window[1]))
        self._walk_m = walk_m
        self._rounds = max_transfers + 1
        self._journeys_max = journeys_max
        self._walk = walk
        self._transfer_walk_m = transfer_walk_m
        self._stop_fields: dict[str, WalkField] = {}
        self._stop_walks: dict[StopPair, Walk] = {}
        self._backward = _Network(feed.patterns, self._footpaths(), reverse=True)
        self._route_patterns: dict[str, list[Pattern]] = defaultdict(list)
        for pattern in feed.patterns:
            self._route_patterns[pattern.route_id].append(pattern)
        self._timetables: dict[tuple[str, str, str], tuple[list[int], list[int]]] = {}

    def target(self, lat: float, lon: float) -> Target:
        return self.nearest([(lat, lon)])

    def nearest(self, points: Sequence[Point]) -> Target:
        """One target for many points: a journey ends at whichever of them it reaches first."""
        egress: dict[str, tuple[int, Walk]] = {}
        for index, point in enumerate(points):
            field = self._walk.field(*point, self._walk_m * FIELD_REACH) if self._walk else None
            for stop in self._feed.stops_near(*point, self._walk_m):
                walk = self._walk_to(field, point, stop)
                if stop not in egress or walk.m < egress[stop][1].m:
                    egress[stop] = (index, walk)
        runs: list[Labels] = []
        start, end = self._window
        for deadline in range(start, end + DEADLINE_SLACK_S + 1, DEADLINE_STEP_S):
            origins = {stop: -(deadline - _seconds(walk)) for stop, (_, walk) in egress.items()}
            runs.append(self._backward.raptor(origins, self._rounds))
        return Target(egress=egress, runs=runs, templates={})

    def options(self, target: Target, lat: float, lon: float) -> list[Journey]:
        home = self._feed.stops_near(lat, lon, self._walk_m)
        if not home or not target.egress:
            return []
        templates: set[tuple[LegTemplate, ...]] = set()
        for run_index, labels in enumerate(target.runs):
            for round_index in range(1, len(labels)):
                for stop in home:
                    label = labels[round_index].get(stop)
                    if label is None or label[1] is None or label[1][0] != "ride":
                        continue
                    key = (run_index, round_index, stop)
                    if key not in target.templates:
                        target.templates[key] = self._template(labels, round_index, stop)
                    templates.add(target.templates[key])
        grouped: dict[tuple[StopPair, ...], list[dict[str, LegTemplate]]] = {}
        for template in templates:
            pairs = tuple(
                (self._feed.patterns[index].stops[board], self._feed.patterns[index].stops[alight])
                for index, board, alight in template
            )
            legs = grouped.setdefault(pairs, [{} for _ in template])
            for position, leg in enumerate(template):
                legs[position].setdefault(self._feed.patterns[leg[0]].route_id, leg)
        field = self._walk.field(lat, lon, self._walk_m * FIELD_REACH) if self._walk else None
        measured = [
            journey
            for pairs, legs in grouped.items()
            if (journey := self._measure(pairs, legs, (lat, lon), field, target)) is not None
        ]
        kept = [journey for journey in measured if not _absorbed(journey, measured)]
        return sorted(kept, key=_order)[: self._journeys_max]

    def _template(self, labels: Labels, round_index: int, stop: str) -> tuple[LegTemplate, ...]:
        legs: list[LegTemplate] = []
        while True:
            _, parent = labels[round_index][stop]
            if parent is None:
                return tuple(legs)
            if parent[0] == "walk":
                stop = parent[1]
                continue
            _, pattern_index, _, board_index, alight_index = parent
            last = len(self._feed.patterns[pattern_index].stops) - 1
            legs.append((pattern_index, last - alight_index, last - board_index))
            stop = self._backward.stops[pattern_index][board_index]
            round_index -= 1

    def _measure(
        self,
        pairs: tuple[StopPair, ...],
        leg_routes: list[dict[str, LegTemplate]],
        home: Point,
        home_field: WalkField | None,
        target: Target,
    ) -> Journey | None:
        feed = self._feed
        start, end = self._window
        timetables: list[tuple[list[int], list[int]]] = []
        headways: list[int] = []
        drawn: list[LegTemplate] = []
        for (board, alight), routes in zip(pairs, leg_routes):
            calls: list[tuple[int, int]] = []
            busiest, busiest_count = None, 0
            for route_id in list(routes):
                departures, arrivals = self._timetable(route_id, board, alight)
                count = bisect_left(departures, end) - bisect_left(departures, start)
                if count == 0:
                    del routes[route_id]
                    continue
                if count > busiest_count:
                    busiest, busiest_count = routes[route_id], count
                calls += zip(departures, arrivals)
            if busiest is None:
                return None
            calls.sort()
            departures = [departure for departure, _ in calls]
            count = bisect_left(departures, end) - bisect_left(departures, start)
            timetables.append((departures, [arrival for _, arrival in calls]))
            headways.append(max(1, round((end - start) / 60 / count)))
            drawn.append(busiest)
        walks = [
            self._walk_to(home_field, home, pairs[0][0]),
            *(
                self._stop_walk(alight, next_board)
                for (_, alight), (next_board, _) in zip(pairs, pairs[1:])
            ),
            _reversed(target.egress[pairs[-1][1]][1]),
        ]
        transfers = [max(MIN_TRANSFER_S, _seconds(walk)) for walk in walks[1:-1]]
        first_departures, first_arrivals = timetables[0]
        totals = []
        for index in range(bisect_left(first_departures, start), bisect_left(first_departures, end)):
            departed, clock = first_departures[index], first_arrivals[index]
            for (departures, arrivals), wait in zip(timetables[1:], transfers):
                position = bisect_left(departures, clock + wait)
                if position == len(departures):
                    clock = None
                    break
                clock = arrivals[position]
            if clock is not None:
                totals.append(clock - departed)
        if not totals:
            return None
        legs = []
        for (board, alight), routes, headway, (pattern_index, board_index, alight_index) in zip(
            pairs, leg_routes, headways, drawn
        ):
            pattern = feed.patterns[pattern_index]
            labels = {self._route_patterns[route_id][0].label for route_id in routes}
            legs.append(
                Leg(
                    routes=tuple(sorted(labels, key=_label_order)),
                    board=board,
                    alight=alight,
                    every_min=headway,
                    shape=pattern.shape,
                    span=(pattern.shape_indices[board_index], pattern.shape_indices[alight_index])
                    if pattern.shape
                    else None,
                )
            )
        ends_s = _seconds(walks[0]) + _seconds(walks[-1])
        return Journey(
            minutes=round((median(totals) + ends_s) / 60),
            every_min=max(headways),
            transfers=len(legs) - 1,
            walk_m=sum(walk.m for walk in walks),
            legs=tuple(legs),
            walks=tuple(walks),
        )

    def _timetable(self, route_id: str, board: str, alight: str) -> tuple[list[int], list[int]]:
        key = (route_id, board, alight)
        if key not in self._timetables:
            calls: list[tuple[int, int]] = []
            for pattern in self._route_patterns[route_id]:
                if board not in pattern.stops or alight not in pattern.stops:
                    continue
                board_index = pattern.stops.index(board)
                alight_index = pattern.stops.index(alight)
                if alight_index <= board_index:
                    continue
                calls += [
                    (trip.departures[board_index], trip.arrivals[alight_index])
                    for trip in pattern.trips
                ]
            calls.sort()
            self._timetables[key] = ([dep for dep, _ in calls], [arr for _, arr in calls])
        return self._timetables[key]

    def _footpaths(self) -> dict[str, list[tuple[str, float]]]:
        if self._walk is None:
            return dict(self._feed.footpaths)
        return {
            stop: sorted(
                (
                    (other, self._stop_walk(stop, other).m / WALK_SPEED_M_PER_MIN)
                    for other, _ in near
                ),
                key=lambda item: item[1],
            )
            for stop, near in self._feed.footpaths.items()
        }

    def _stop_walk(self, stop: str, other: str) -> Walk:
        key = (stop, other)
        if key not in self._stop_walks:
            if self._walk is None:
                self._stop_walks[key] = _straight(self._feed.stops[stop], self._feed.stops[other])
            else:
                if stop not in self._stop_fields:
                    self._stop_fields[stop] = self._walk.field(
                        *self._feed.stops[stop], self._transfer_walk_m * FIELD_REACH
                    )
                self._stop_walks[key] = self._stop_fields[stop].to(*self._feed.stops[other])
        return self._stop_walks[key]

    def _walk_to(self, field: WalkField | None, origin: Point, stop: str) -> Walk:
        if field is None:
            return _straight(origin, self._feed.stops[stop])
        return field.to(*self._feed.stops[stop])


class _Network:
    def __init__(
        self,
        patterns: Sequence[Pattern],
        footpaths: Mapping[str, Sequence[tuple[str, float]]],
        reverse: bool,
    ) -> None:
        self.stops: list[tuple[str, ...]] = []
        self.trips: list[list[tuple[tuple[int, ...], tuple[int, ...]]]] = []
        self.departures_at: list[list[list[tuple[int, int]]]] = []
        self.stop_patterns: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.footpaths = {
            stop: [(other, round(minutes * 60)) for other, minutes in near]
            for stop, near in footpaths.items()
        }
        for index, pattern in enumerate(patterns):
            stops = tuple(reversed(pattern.stops)) if reverse else pattern.stops
            trips = [
                (
                    tuple(-time for time in reversed(trip.arrivals)),
                    tuple(-time for time in reversed(trip.departures)),
                )
                if reverse
                else (trip.departures, trip.arrivals)
                for trip in pattern.trips
            ]
            trips.sort(key=lambda trip: trip[0][0])
            self.stops.append(stops)
            self.trips.append(trips)
            self.departures_at.append(
                [
                    sorted((trip[0][position], trip_index) for trip_index, trip in enumerate(trips))
                    for position in range(len(stops))
                ]
            )
            for position, stop in enumerate(stops):
                self.stop_patterns[stop].append((index, position))

    def raptor(self, origins: Mapping[str, int], rounds: int) -> Labels:
        labels: Labels = [{stop: (time, None) for stop, time in origins.items()}]
        best = dict(origins)
        marked = set(origins)
        for round_index in range(1, rounds + 1):
            queue: dict[int, int] = {}
            for stop in marked:
                for pattern_index, position in self.stop_patterns[stop]:
                    queue[pattern_index] = min(queue.get(pattern_index, position), position)
            current: dict[str, tuple[int, Parent]] = {}
            improved: set[str] = set()
            for pattern_index, first in queue.items():
                stops, trips = self.stops[pattern_index], self.trips[pattern_index]
                trip_index = board_position = -1
                for position in range(first, len(stops)):
                    stop = stops[position]
                    if trip_index >= 0:
                        arrival = trips[trip_index][1][position]
                        if arrival < best.get(stop, arrival + 1):
                            best[stop] = arrival
                            current[stop] = (
                                arrival,
                                ("ride", pattern_index, trip_index, board_position, position),
                            )
                            improved.add(stop)
                    earlier = labels[round_index - 1].get(stop)
                    if earlier is None:
                        continue
                    if trip_index >= 0 and earlier[0] >= trips[trip_index][0][position]:
                        continue
                    departures = self.departures_at[pattern_index][position]
                    slot = bisect_left(departures, (earlier[0], -1))
                    if slot < len(departures) and (
                        trip_index < 0 or departures[slot][0] < trips[trip_index][0][position]
                    ):
                        trip_index, board_position = departures[slot][1], position
            marked = set(improved)
            for stop in improved:
                time = current[stop][0]
                for other, seconds in self.footpaths.get(stop, ()):
                    if time + seconds < best.get(other, time + seconds + 1):
                        best[other] = time + seconds
                        current[other] = (time + seconds, ("walk", stop))
                        marked.add(other)
            labels.append(current)
            if not marked:
                break
        return labels


def _straight(origin: Point, destination: Point) -> Walk:
    metres = distance_m(*origin, *destination) * ROAD_DETOUR
    return Walk(round(metres), [origin, destination], False)


def _reversed(walk: Walk) -> Walk:
    return Walk(walk.m, list(reversed(walk.points)), walk.on_streets)


def _seconds(walk: Walk) -> int:
    return round(walk.m / WALK_SPEED_M_PER_MIN * 60)


def _absorbed(journey: Journey, others: Sequence[Journey]) -> bool:
    return any(
        other is not journey
        and len(other.legs) == len(journey.legs)
        and all(set(mine.routes) <= set(theirs.routes) for mine, theirs in zip(journey.legs, other.legs))
        and _order(other) <= _order(journey)
        for other in others
    )


def _order(journey: Journey) -> tuple[float, int, int]:
    return journey.minutes + journey.every_min / 2, journey.minutes, journey.transfers


def _label_order(label: str) -> tuple[str, int, str]:
    mode, _, name = label.partition(" ")
    digits = "".join(character for character in name if character.isdigit())
    return mode, int(digits) if digits else 0, name


def _clock_seconds(text: str) -> int:
    hours, _, minutes = text.partition(":")
    return int(hours) * 3600 + int(minutes or 0) * 60
