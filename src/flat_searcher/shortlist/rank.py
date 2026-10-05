"""Hard gates and the ordering of the candidates that pass them."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from flat_searcher.shortlist.criteria import Criteria
from flat_searcher.shortlist.facts import (
    LAYOUT_RANK,
    Fact,
    heating_fact,
    hot_water_fact,
    land_fact,
    layout_fact,
    replanning_fact,
    sells_share,
    stove_fact,
)

GATES = ("price", "rooms", "floor", "building_type", "stove", "share")


@dataclass(frozen=True)
class Evaluation:
    ss_id: str
    record: Mapping[str, Any]
    rejected: str | None
    band: int | None
    rooms_index: int
    layout: Fact
    heating: Fact
    land: Fact = Fact("unknown", "none")
    stove: Fact = Fact("none", "none")
    hot_water: Fact = Fact("unknown", "none")
    replanning: Fact = Fact("none", "none")
    journeys: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    precision: str | None = None
    price_ratio: float | None = None

    @property
    def reached(self) -> int:
        return sum(
            1
            for options in self.journeys.values()
            if any(option.get("transfers") == 0 for option in options)
        )


def evaluate(
    record: Mapping[str, Any], criteria: Criteria, transit: Mapping[str, Any] | None
) -> Evaluation:
    core = record.get("core") or {}
    price = core.get("price_eur")
    rooms = core.get("declared_rooms")
    band = criteria.band_index(price) if isinstance(price, (int, float)) else None
    heating = heating_fact(record)
    share = sells_share(record)
    checks = {
        "price": band is None,
        "rooms": bool(criteria.room_order) and rooms is not None and rooms not in criteria.room_order,
        "floor": core.get("floor") in criteria.excluded_floors,
        "building_type": core.get("building_type") in criteria.excluded_types,
        "stove": criteria.exclude_stove and heating.value == "stove",
        "share": share.value == "yes",
    }
    rejected = next((gate for gate in GATES if checks[gate]), None)
    rooms_index = (
        criteria.room_order.index(rooms) if rooms in criteria.room_order else len(criteria.room_order)
    )
    return Evaluation(
        ss_id=str(record.get("ss_id")),
        record=record,
        rejected=rejected,
        band=band,
        rooms_index=rooms_index,
        layout=layout_fact(record),
        heating=heating,
        land=land_fact(record),
        stove=stove_fact(record),
        hot_water=hot_water_fact(record),
        replanning=replanning_fact(record),
        journeys=dict((transit or {}).get("targets") or {}),
        precision=(transit or {}).get("precision"),
        price_ratio=_price_ratio(record),
    )


def sort_key(evaluation: Evaluation) -> tuple[Any, ...]:
    price = (evaluation.record.get("core") or {}).get("price_eur") or 0
    return (
        evaluation.band if evaluation.band is not None else math.inf,
        evaluation.rooms_index,
        -LAYOUT_RANK[(evaluation.layout.value, evaluation.layout.source)],
        -evaluation.reached,
        evaluation.price_ratio if evaluation.price_ratio is not None else math.inf,
        price,
        evaluation.ss_id,
    )


def expected_minutes(option: Mapping[str, Any]) -> float:
    """Door to door plus the average wait when leaving at a random moment."""
    return option["minutes"] + option["every_min"] / 2


def total_expected_minutes(
    journeys: Mapping[str, Sequence[Mapping[str, Any]]], target_names: Sequence[str]
) -> float | None:
    best = [min(map(expected_minutes, journeys.get(name) or []), default=None) for name in target_names]
    return None if None in best else sum(value for value in best if value is not None)


def _price_ratio(record: Mapping[str, Any]) -> float | None:
    market = record.get("market") or {}
    value = market.get("price_per_m2")
    median = market.get("district_median_price_per_m2")
    if not isinstance(value, (int, float)) or not isinstance(median, (int, float)) or not median:
        return None
    return round(float(value) / float(median), 3)
