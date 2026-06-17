"""Geographic distance helpers."""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt


EARTH_RADIUS_M = 6_371_000


@dataclass(frozen=True)
class Coordinate:
    latitude: float
    longitude: float


def haversine_distance_m(origin: Coordinate, destination: Coordinate) -> float:
    lat1 = radians(origin.latitude)
    lat2 = radians(destination.latitude)
    delta_lat = radians(destination.latitude - origin.latitude)
    delta_lon = radians(destination.longitude - origin.longitude)

    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(value))
