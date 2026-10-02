"""Distances, walking time, line simplification and the encoded-polyline format."""

from __future__ import annotations

import math
from collections.abc import Sequence

EARTH_RADIUS_M = 6_371_000
METRES_PER_DEGREE = 111_195
WALK_SPEED_M_PER_MIN = 5000 / 60
ROAD_DETOUR = 1.3
POLYLINE_PRECISION = 1e5

Point = tuple[float, float]


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def walk_minutes(distance: float) -> float:
    return distance * ROAD_DETOUR / WALK_SPEED_M_PER_MIN


def simplify(points: Sequence[Point], tolerance_m: float) -> list[Point]:
    if len(points) < 3:
        return list(points)
    scale = math.cos(math.radians(points[0][0]))
    flat = [(lon * scale, lat) for lat, lon in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    pending = [(0, len(points) - 1)]
    tolerance = tolerance_m / METRES_PER_DEGREE
    while pending:
        start, end = pending.pop()
        farthest, index = 0.0, -1
        for candidate in range(start + 1, end):
            offset = _offset(flat[candidate], flat[start], flat[end])
            if offset > farthest:
                farthest, index = offset, candidate
        if farthest > tolerance:
            keep[index] = True
            pending += [(start, index), (index, end)]
    return [point for point, kept in zip(points, keep) if kept]


def encode_polyline(points: Sequence[Point]) -> str:
    output: list[str] = []
    previous = (0, 0)
    for lat, lon in points:
        current = (round(lat * POLYLINE_PRECISION), round(lon * POLYLINE_PRECISION))
        for value, earlier in zip(current, previous):
            delta = value - earlier
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                output.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            output.append(chr(delta + 63))
        previous = current
    return "".join(output)


def decode_polyline(text: str) -> list[Point]:
    points: list[Point] = []
    lat = lon = 0
    position = 0
    while position < len(text):
        deltas = []
        for _ in range(2):
            result = shift = 0
            while True:
                chunk = ord(text[position]) - 63
                position += 1
                result |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / POLYLINE_PRECISION, lon / POLYLINE_PRECISION))
    return points


def _offset(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    along = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    along = max(0.0, min(1.0, along))
    return math.hypot(point[0] - start[0] - along * dx, point[1] - start[1] - along * dy)
