"""What is near a flat: shops and gyms on foot and by car, those on the way to each target, nuisances around."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from flat_searcher.transit.driving import DriveField, DriveGraph
from flat_searcher.transit.geometry import (
    METRES_PER_DEGREE,
    WALK_SPEED_M_PER_MIN,
    Point,
    decode_polyline,
    distance_m,
)
from flat_searcher.transit.walking import WalkGraph

WALK_LIMIT_M = 2500
WALK_NEAREST = {"grocery": 3, "gym": 3, "mall": 1}
DRIVE_NEAREST = {"grocery": 3, "diy": 3, "mall": 2}
DRIVE_RADIUS_M = 15000
ON_THE_WAY_S = {"grocery": 180, "mall": 180, "diy": 300}
WALK_PATH_M = 100
AROUND_SHOWN_M = {
    "industrial": 500,
    "works": 500,
    "cemetery": 500,
    "railway": 300,
    "bog": 2000,
    "landfill": 3000,
    "wastewater": 3000,
}
MIN_DRIVE_LIMIT_S = 1200
DETOUR_HEADROOM_S = 300
CELL_DEGREES = 0.02


@dataclass(frozen=True)
class Place:
    category: str
    name: str
    lat: float
    lon: float

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True)
class Area:
    kind: str
    name: str
    ring: tuple[Point, ...]
    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True)
class TargetFields:
    """Car times from every node to a target and from the target to every node."""

    point: Point
    to: DriveField
    back: DriveField


class Places:
    def __init__(self, data: Mapping[str, Any]) -> None:
        self._grid: dict[tuple[str, int, int], list[Place]] = {}
        self.by_category: dict[str, list[Place]] = {}
        for category, name, lat, lon in data.get("pois") or []:
            place = Place(str(category), str(name), float(lat), float(lon))
            self.by_category.setdefault(place.category, []).append(place)
            self._grid.setdefault((place.category, *_cell(place.lat, place.lon)), []).append(place)
        self._areas: list[Area] = []
        for kind, name, line, _hectares in data.get("areas") or []:
            ring = tuple(decode_polyline(str(line)))
            if ring and kind in AROUND_SHOWN_M:
                lats = [lat for lat, _ in ring]
                lons = [lon for _, lon in ring]
                self._areas.append(Area(str(kind), str(name), ring, min(lats), min(lons), max(lats), max(lons)))

    def near(self, category: str, lat: float, lon: float, radius_m: float) -> list[Place]:
        reach = math.ceil(radius_m / METRES_PER_DEGREE / CELL_DEGREES / math.cos(math.radians(lat)))
        row, column = _cell(lat, lon)
        found = []
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                for place in self._grid.get((category, row + dr, column + dc), ()):
                    if distance_m(lat, lon, place.lat, place.lon) <= radius_m:
                        found.append(place)
        return found

    def around(self, lat: float, lon: float) -> dict[str, dict[str, Any]]:
        nearest: dict[str, dict[str, Any]] = {}
        for area in self._areas:
            shown = AROUND_SHOWN_M[area.kind]
            if _box_distance_m(lat, lon, area) > shown:
                continue
            metres, at = _area_distance(lat, lon, area.ring)
            if metres <= shown and metres < nearest.get(area.kind, {}).get("m", math.inf):
                nearest[area.kind] = {"name": area.name, "m": round(metres), "lat": at[0], "lon": at[1]}
        return dict(sorted(nearest.items(), key=lambda item: item[1]["m"]))


def target_fields(drive: DriveGraph, lat: float, lon: float) -> TargetFields:
    return TargetFields((lat, lon), drive.field(lat, lon, reverse=True), drive.field(lat, lon))


def surroundings(
    point: Point,
    walk: WalkGraph,
    drive: DriveGraph,
    places: Places,
    targets: Mapping[str, TargetFields],
    first_walks: Mapping[str, str],
) -> dict[str, Any]:
    lat, lon = point
    result: dict[str, Any] = {
        "walk": _walk_nearest(point, walk, places),
        "around": places.around(lat, lon),
    }
    legs = {name: (fields.to.at(lat, lon), fields.back.at(lat, lon)) for name, fields in targets.items()}
    reached = [leg.seconds for there, back in legs.values() for leg in (there, back) if leg]
    limit = max(MIN_DRIVE_LIMIT_S, max(reached, default=0) + DETOUR_HEADROOM_S)
    out = drive.field(lat, lon, limit)
    home = drive.field(lat, lon, limit, reverse=True)
    drive_part: dict[str, Any] = {"targets": {}}
    for name, (there, back) in legs.items():
        if there and back:
            drive_part["targets"][name] = {
                "min": there.minutes, "km": there.km, "back_min": back.minutes, "back_km": back.km,
            }
    for category, count in DRIVE_NEAREST.items():
        timed = [
            (leg.seconds, leg, place)
            for place in places.near(category, lat, lon, DRIVE_RADIUS_M)
            if (leg := out.at(place.lat, place.lon)) is not None
        ]
        timed.sort(key=lambda item: item[0])
        drive_part[category] = [
            {**place.as_dict(), "min": leg.minutes, "km": leg.km} for _, leg, place in timed[:count]
        ]
    result["drive"] = drive_part
    result["on_the_way"] = {
        name: _on_the_way(fields, legs[name], out, home, places, first_walks.get(name))
        for name, fields in targets.items()
    }
    return result


def _walk_nearest(point: Point, walk: WalkGraph, places: Places) -> dict[str, list[dict[str, Any]]]:
    field = walk.field(*point, WALK_LIMIT_M)
    nearest: dict[str, list[dict[str, Any]]] = {}
    for category, count in WALK_NEAREST.items():
        walks = []
        for place in places.near(category, *point, WALK_LIMIT_M):
            route = field.to(place.lat, place.lon)
            if route.on_streets and route.m <= WALK_LIMIT_M:
                walks.append((route.m, place))
        walks.sort(key=lambda item: item[0])
        nearest[category] = [
            {**place.as_dict(), "min": max(1, round(metres / WALK_SPEED_M_PER_MIN)), "m": metres}
            for metres, place in walks[:count]
        ]
    return nearest


def _on_the_way(
    fields: TargetFields,
    legs: tuple[Any, Any],
    out: DriveField,
    home: DriveField,
    places: Places,
    first_walk: str | None,
) -> dict[str, Any]:
    there, back = legs
    found: dict[str, Any] = {"there": {}, "back": {}}
    for category, allowed in ON_THE_WAY_S.items():
        for direction, direct, first, second in (
            ("there", there, out, fields.to),
            ("back", back, fields.back, home),
        ):
            if direct is None:
                continue
            best: tuple[tuple[int, bool, int], Place] | None = None
            for place in places.by_category.get(category, ()):
                reach, onward = first.at(place.lat, place.lon), second.at(place.lat, place.lon)
                if reach is None or onward is None:
                    continue
                detour = reach.seconds + onward.seconds - direct.seconds
                rank = (max(0, round(detour / 60)), not place.name, detour)
                if detour <= allowed and (best is None or rank < best[0]):
                    best = (rank, place)
            if best is not None:
                found[direction][category] = {**best[1].as_dict(), "plus_min": best[0][0]}
    if first_walk:
        path = decode_polyline(first_walk)
        beside = [
            (metres, place)
            for place in places.near("grocery", *path[0], WALK_LIMIT_M)
            if (metres := _path_distance(place.lat, place.lon, path)) <= WALK_PATH_M
        ]
        if beside:
            found["on_foot"] = {"grocery": min(beside, key=lambda item: item[0])[1].as_dict()}
    return found


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return int(lat // CELL_DEGREES), int(lon // CELL_DEGREES)


def _box_distance_m(lat: float, lon: float, area: Area) -> float:
    nearest_lat = min(max(lat, area.south), area.north)
    nearest_lon = min(max(lon, area.west), area.east)
    return distance_m(lat, lon, nearest_lat, nearest_lon)


def _area_distance(lat: float, lon: float, ring: Sequence[Point]) -> tuple[float, Point]:
    if len(ring) >= 4 and _inside(lat, lon, ring):
        return 0.0, (lat, lon)
    if len(ring) == 1:
        return distance_m(lat, lon, *ring[0]), ring[0]
    return min((_segment(lat, lon, a, b) for a, b in zip(ring, ring[1:])), key=lambda item: item[0])


def _path_distance(lat: float, lon: float, path: Sequence[Point]) -> float:
    if len(path) == 1:
        return distance_m(lat, lon, *path[0])
    return min(_segment(lat, lon, a, b)[0] for a, b in zip(path, path[1:]))


def _segment(lat: float, lon: float, a: Point, b: Point) -> tuple[float, Point]:
    scale = math.cos(math.radians(lat))
    ax, ay = (a[1] - lon) * scale, a[0] - lat
    bx, by = (b[1] - lon) * scale, b[0] - lat
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    at = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return distance_m(lat, lon, *at), at


def _inside(lat: float, lon: float, ring: Sequence[Point]) -> bool:
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(ring, ring[1:]):
        if (lat1 > lat) != (lat2 > lat):
            crossing = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            if lon < crossing:
                inside = not inside
    return inside
