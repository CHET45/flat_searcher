"""A GTFS feed reduced to one service day: patterns with times, shapes and footpaths."""

from __future__ import annotations

import csv
import io
import math
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from flat_searcher.transit.geometry import METRES_PER_DEGREE, Point, distance_m, walk_minutes

MODES = {"0": "tram", "3": "bus", "11": "trolleybus", "800": "trolleybus"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
REFERENCE_DAY_HORIZON = timedelta(days=60)


@dataclass(frozen=True)
class Trip:
    departures: tuple[int, ...]
    arrivals: tuple[int, ...]


@dataclass(frozen=True)
class Pattern:
    route_id: str
    label: str
    stops: tuple[str, ...]
    trips: tuple[Trip, ...]
    shape: str | None
    shape_indices: tuple[int, ...]


@dataclass(frozen=True)
class TransitFeed:
    day: date
    label: str
    stops: dict[str, Point]
    stop_names: dict[str, str]
    patterns: tuple[Pattern, ...]
    shapes: dict[str, list[Point]]
    footpaths: dict[str, list[tuple[str, float]]]

    @classmethod
    def from_zip(cls, data: bytes, today: date, transfer_walk_m: float) -> TransitFeed:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())

            def rows(name: str) -> Iterator[dict[str, str]]:
                if name not in names:
                    return iter(())
                text = archive.read(name).decode("utf-8-sig")
                return csv.DictReader(io.StringIO(text, newline=""))

            stops, stop_names = {}, {}
            for row in rows("stops.txt"):
                stops[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))
                stop_names[row["stop_id"]] = row.get("stop_name") or row["stop_id"]
            route_labels = {
                row["route_id"]: f"{MODES.get(row['route_type'], row['route_type'])} "
                f"{row['route_short_name']}"
                for row in rows("routes.txt")
            }
            exceptions = list(rows("calendar_dates.txt"))
            day = reference_day(today, {row["date"] for row in exceptions})
            calendar = list(rows("calendar.txt"))
            services = _active_services(calendar, exceptions, day)
            label = _service_period(calendar, today)
            trip_route, trip_shape = {}, {}
            for row in rows("trips.txt"):
                if row["service_id"] in services and row["route_id"] in route_labels:
                    trip_route[row["trip_id"]] = (row["route_id"], row.get("direction_id") or "0")
                    trip_shape[row["trip_id"]] = row.get("shape_id") or None
            visits: dict[str, list[tuple[int, str, int | None, int | None]]] = defaultdict(list)
            for row in rows("stop_times.txt"):
                if row["trip_id"] in trip_route:
                    visits[row["trip_id"]].append(
                        (
                            int(row["stop_sequence"]),
                            row["stop_id"],
                            _seconds(row.get("arrival_time") or row.get("departure_time") or ""),
                            _seconds(row.get("departure_time") or row.get("arrival_time") or ""),
                        )
                    )
            shape_points: dict[str, list[tuple[int, Point]]] = defaultdict(list)
            for row in rows("shapes.txt"):
                shape_points[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), (float(row["shape_pt_lat"]), float(row["shape_pt_lon"])))
                )
        shapes = {
            shape_id: [point for _, point in sorted(points)]
            for shape_id, points in shape_points.items()
        }
        patterns = _patterns(visits, trip_route, trip_shape, route_labels, stops, shapes)
        return cls(
            day=day,
            label=label,
            stops=stops,
            stop_names=stop_names,
            patterns=patterns,
            shapes=shapes,
            footpaths=_footpaths(stops, transfer_walk_m),
        )

    def stops_near(self, lat: float, lon: float, radius_m: float) -> dict[str, float]:
        found = {
            stop_id: distance
            for stop_id, (stop_lat, stop_lon) in self.stops.items()
            if (distance := distance_m(lat, lon, stop_lat, stop_lon)) <= radius_m
        }
        return dict(sorted(found.items(), key=lambda item: item[1]))


def reference_day(today: date, exception_dates: set[str]) -> date:
    candidate = today
    while candidate - today < REFERENCE_DAY_HORIZON:
        if candidate.weekday() < 5 and candidate.strftime("%Y%m%d") not in exception_dates:
            return candidate
        candidate += timedelta(days=1)
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _active_services(
    calendar: list[dict[str, str]], exceptions: list[dict[str, str]], day: date
) -> set[str]:
    key = day.strftime("%Y%m%d")
    active = {
        row["service_id"]
        for row in calendar
        if row.get(WEEKDAYS[day.weekday()]) == "1" and row["start_date"] <= key <= row["end_date"]
    }
    for row in exceptions:
        if row["date"] != key:
            continue
        if row["exception_type"] == "1":
            active.add(row["service_id"])
        else:
            active.discard(row["service_id"])
    return active


def _service_period(calendar: list[dict[str, str]], today: date) -> str:
    key = today.strftime("%Y%m%d")
    current = [row for row in calendar if row["end_date"] >= key]
    if not current:
        return "no-calendar"
    return f"{min(row['start_date'] for row in current)}-{max(row['end_date'] for row in current)}"


def _patterns(
    visits: dict[str, list[tuple[int, str, int | None, int | None]]],
    trip_route: dict[str, tuple[str, str]],
    trip_shape: dict[str, str | None],
    route_labels: dict[str, str],
    stops: dict[str, Point],
    shapes: dict[str, list[Point]],
) -> tuple[Pattern, ...]:
    grouped: dict[tuple[str, str, tuple[str, ...]], list[Trip]] = defaultdict(list)
    group_shape: dict[tuple[str, str, tuple[str, ...]], str | None] = {}
    for trip_id, calls in visits.items():
        calls.sort()
        if any(arrival is None or departure is None for _, _, arrival, departure in calls):
            continue
        sequence = tuple(stop_id for _, stop_id, _, _ in calls)
        key = (*trip_route[trip_id], sequence)
        grouped[key].append(
            Trip(
                departures=tuple(departure for _, _, _, departure in calls if departure is not None),
                arrivals=tuple(arrival for _, _, arrival, _ in calls if arrival is not None),
            )
        )
        group_shape.setdefault(key, trip_shape[trip_id])
    patterns = []
    for (route_id, direction, sequence), trips in sorted(grouped.items()):
        shape = group_shape[(route_id, direction, sequence)]
        if shape not in shapes:
            shape = None
        patterns.append(
            Pattern(
                route_id=route_id,
                label=route_labels[route_id],
                stops=sequence,
                trips=tuple(sorted(trips, key=lambda trip: trip.departures[0])),
                shape=shape,
                shape_indices=_shape_indices(sequence, stops, shapes[shape]) if shape else (),
            )
        )
    return tuple(patterns)


def _shape_indices(
    sequence: Iterable[str], stops: dict[str, Point], points: list[Point]
) -> tuple[int, ...]:
    scale = math.cos(math.radians(points[0][0]))
    indices = []
    start = 0
    for stop_id in sequence:
        lat, lon = stops[stop_id]
        nearest = min(
            range(start, len(points)),
            key=lambda index: (points[index][0] - lat) ** 2
            + ((points[index][1] - lon) * scale) ** 2,
        )
        indices.append(nearest)
        start = nearest
    return tuple(indices)


def _footpaths(stops: dict[str, Point], radius_m: float) -> dict[str, list[tuple[str, float]]]:
    cell = radius_m / METRES_PER_DEGREE
    grid: dict[tuple[int, int], list[str]] = defaultdict(list)
    for stop_id, (lat, lon) in stops.items():
        grid[(int(lat // cell), int(lon // cell))].append(stop_id)
    paths: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for stop_id, (lat, lon) in stops.items():
        row, column = int(lat // cell), int(lon // cell)
        for neighbour_cell in ((row + dr, column + dc) for dr in (-1, 0, 1) for dc in (-2, -1, 0, 1, 2)):
            for other in grid.get(neighbour_cell, ()):
                if other == stop_id:
                    continue
                distance = distance_m(lat, lon, *stops[other])
                if distance <= radius_m:
                    paths[stop_id].append((other, walk_minutes(distance)))
    return {stop_id: sorted(near, key=lambda item: item[1]) for stop_id, near in paths.items()}


def _seconds(clock: str) -> int | None:
    parts = clock.strip().split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds
