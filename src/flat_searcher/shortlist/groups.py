"""Ads that describe one flat: same building, floor and rooms; the same area with prices within 10 %, or
the same price with areas within a square metre (two sellers measuring one flat differently)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from flat_searcher.shortlist.rank import Evaluation

PRICE_SPREAD = 0.10
AREA_TOLERANCE_M2 = 1.0


def group_ads(candidates: Sequence[Evaluation]) -> list[list[Evaluation]]:
    """Groups in the order of their first ad; each group keeps the candidates' order."""
    by_flat: dict[tuple[Any, ...], list[Evaluation]] = defaultdict(list)
    for item in candidates:
        key = _flat_key(item.record)
        if key is not None:
            by_flat[key].append(item)
    parent: dict[str, str] = {}

    def root(ss_id: str) -> str:
        while parent.get(ss_id, ss_id) != ss_id:
            ss_id = parent[ss_id]
        return ss_id

    def join(first: str, second: str) -> None:
        parent[root(second)] = root(first)

    for ads in by_flat.values():
        leader, cheapest = "", 0.0
        for item in sorted(ads, key=_price):
            if not leader or _price(item) > cheapest * (1 + PRICE_SPREAD):
                leader, cheapest = item.ss_id, _price(item)
            join(leader, item.ss_id)
    by_floor: dict[tuple[Any, ...], list[Evaluation]] = defaultdict(list)
    for key, ads in by_flat.items():
        by_floor[(key[0], key[1], key[2], key[4])].extend(ads)
    for ads in by_floor.values():
        for index, item in enumerate(ads):
            for other in ads[index + 1 :]:
                if _price(item) == _price(other) and abs(_area(item) - _area(other)) <= AREA_TOLERANCE_M2:
                    join(item.ss_id, other.ss_id)
    groups: dict[str, list[Evaluation]] = {}
    for item in candidates:
        groups.setdefault(root(item.ss_id), []).append(item)
    return list(groups.values())


def _price(item: Evaluation) -> float:
    return float((item.record.get("core") or {}).get("price_eur") or 0)


def _area(item: Evaluation) -> float:
    return float((item.record.get("core") or {}).get("area_m2") or 0)


def _flat_key(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    core = record.get("core") or {}
    street = " ".join(str(core.get("street") or "").split()).casefold()
    house = str(core.get("house_number") or "").strip().casefold()
    values = (core.get("floor"), core.get("area_m2"), core.get("declared_rooms"), core.get("price_eur"))
    if not street or not (house or any(char.isdigit() for char in street)) or None in values:
        return None
    return (street, house, *values[:3])
