"""The short daily view: soft rules, then the flats no other flat beats on price, time and area at once."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from flat_searcher.shortlist.criteria import TodayRules
from flat_searcher.shortlist.digest import suspicion_flags
from flat_searcher.shortlist.rank import Evaluation, total_expected_minutes


@dataclass(frozen=True)
class TodayPick:
    ss_id: str
    front: int


def pick_today(
    items: Sequence[Evaluation], rules: TodayRules, target_names: Sequence[str]
) -> list[TodayPick]:
    """Front 1 is beaten by no other eligible flat; front 2 only by front 1, and so on."""
    remaining = [
        (item, scores)
        for item in items
        if (scores := _scores(item, rules, target_names)) is not None
    ]
    picks: list[TodayPick] = []
    front = 1
    while remaining and len(picks) < rules.size:
        layer = [entry for entry in remaining if not any(_beats(other[1], entry[1]) for other in remaining)]
        picks.extend(TodayPick(item.ss_id, front) for item, _ in layer[: rules.size - len(picks)])
        taken = {item.ss_id for item, _ in layer}
        remaining = [entry for entry in remaining if entry[0].ss_id not in taken]
        front += 1
    return picks


def _scores(
    item: Evaluation, rules: TodayRules, target_names: Sequence[str]
) -> tuple[float, float, float] | None:
    core = item.record.get("core") or {}
    price, area = core.get("price_eur"), core.get("area_m2")
    minutes = total_expected_minutes(item.journeys, target_names)
    if (
        minutes is None
        or not isinstance(price, (int, float))
        or not isinstance(area, (int, float))
        or (rules.max_minutes is not None and minutes > rules.max_minutes)
        or (rules.exclude_walkthrough and item.layout.value == "walkthrough")
        or (rules.exclude_leased_land and item.land.value == "leased")
        or suspicion_flags(item)
    ):
        return None
    return (float(price), minutes, -float(area))


def _beats(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and a != b
