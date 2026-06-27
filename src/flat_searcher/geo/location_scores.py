"""Distance-based MVP location scoring."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShopScoreInput:
    nearest_shop_distance_m: float | None
    shops_within_300m: int = 0
    shops_within_700m: int = 0
    shops_within_1200m: int = 0


@dataclass(frozen=True)
class TransportScoreInput:
    nearest_stop_distance_m: float | None
    stops_nearby_count: int = 0


def rtu_distance_score(distance_m: float | None) -> float | None:
    if distance_m is None:
        return None
    return _piecewise_score(
        distance_m,
        (
            (0, 100),
            (1_000, 100),
            (2_000, 80),
            (4_000, 55),
            (7_000, 25),
            (10_000, 0),
        ),
    )


def central_station_distance_score(distance_m: float | None) -> float | None:
    if distance_m is None:
        return None
    return _piecewise_score(
        distance_m,
        (
            (0, 100),
            (1_000, 100),
            (3_000, 80),
            (6_000, 50),
            (10_000, 20),
            (14_000, 0),
        ),
    )


def shop_score(value: ShopScoreInput) -> float | None:
    if value.nearest_shop_distance_m is None:
        return None
    distance_component = _piecewise_score(
        value.nearest_shop_distance_m,
        (
            (0, 100),
            (300, 100),
            (700, 70),
            (1_200, 35),
            (1_800, 0),
        ),
    )
    shops_300_to_700 = max(0, value.shops_within_700m - value.shops_within_300m)
    shops_700_to_1200 = max(
        0,
        value.shops_within_1200m - value.shops_within_700m,
    )
    count_bonus = min(
        value.shops_within_300m * 8
        + shops_300_to_700 * 3
        + shops_700_to_1200,
        20,
    )
    return min(100.0, distance_component + count_bonus)


def transport_score(value: TransportScoreInput) -> float | None:
    if value.nearest_stop_distance_m is None:
        return None
    distance_component = _piecewise_score(
        value.nearest_stop_distance_m,
        (
            (0, 100),
            (250, 100),
            (500, 75),
            (900, 40),
            (1_500, 0),
        ),
    )
    count_bonus = min(value.stops_nearby_count * 3, 18)
    return min(100.0, distance_component + count_bonus)


def _piecewise_score(distance_m: float, points: tuple[tuple[float, float], ...]) -> float:
    if distance_m <= points[0][0]:
        return points[0][1]
    for (left_distance, left_score), (right_distance, right_score) in zip(points, points[1:]):
        if distance_m <= right_distance:
            span = right_distance - left_distance
            if span <= 0:
                return right_score
            ratio = (distance_m - left_distance) / span
            return left_score + (right_score - left_score) * ratio
    return points[-1][1]
