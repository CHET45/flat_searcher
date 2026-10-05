"""The daily shortlist as a Markdown page."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any, TypeGuard

from flat_searcher.library import ACTIVE
from flat_searcher.shortlist.alerts import alerts_for
from flat_searcher.shortlist.criteria import Criteria
from flat_searcher.shortlist.rank import Evaluation, evaluate, sort_key

NEW_MARK = "★"
DROP_MARK = "↓"
APPROX_MARK = "≈"
NO_ROUTE = "—"
UNKNOWN_CELL = "?"
COLUMNS = ("", "€", "m²", "€/m² vs district", "address", "district", "series / type", "floor", "wear", "layout")


@dataclass(frozen=True)
class DigestCounts:
    active: int
    candidates: int
    new: int
    price_drops: int
    removed: int
    rejected: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Selection:
    candidates: list[Evaluation]
    new_ids: frozenset[str]
    drop_ids: frozenset[str]
    transit: Mapping[str, Mapping[str, Any]]
    previous_day: str | None
    counts: DigestCounts
    price_history: Mapping[str, list[tuple[str, float]]] = field(default_factory=dict)


def select(
    listings: Mapping[str, Mapping[str, Any]],
    criteria: Criteria,
    transit_entries: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    previous_day: str | None,
) -> Selection:
    transit = {str(entry.get("ss_id")): entry for entry in transit_entries}
    events = list(events)
    recent = [event for event in events if previous_day and str(event.get("at", ""))[:10] > previous_day]
    drops = {str(event.get("ss_id")) for event in recent if _price_dropped(event)}
    removed_ids = {str(event.get("ss_id")) for event in recent if event.get("event") == "listing_removed"}

    active = [record for record in listings.values() if record.get("status") == ACTIVE]
    evaluations = [evaluate(record, criteria, transit.get(str(record.get("ss_id")))) for record in active]
    candidates = sorted((item for item in evaluations if item.rejected is None), key=sort_key)
    rejected = Counter(item.rejected for item in evaluations if item.rejected is not None)
    new_ids = {
        item.ss_id
        for item in candidates
        if previous_day and str(item.record.get("first_seen", ""))[:10] > previous_day
    }
    removed = sum(
        1
        for ss_id in removed_ids
        if ss_id in listings and evaluate(listings[ss_id], criteria, None).rejected is None
    )
    candidate_ids = {item.ss_id for item in candidates}
    counts = DigestCounts(
        active=len(active),
        candidates=len(candidates),
        new=len(new_ids),
        price_drops=len(drops & candidate_ids),
        removed=removed,
        rejected={gate: rejected[gate] for gate in sorted(rejected)},
    )
    return Selection(
        candidates=candidates,
        new_ids=frozenset(new_ids),
        drop_ids=frozenset(drops & candidate_ids),
        transit=transit,
        previous_day=previous_day,
        counts=counts,
        price_history=_price_history(events, candidate_ids),
    )


def render_markdown(selection: Selection, criteria: Criteria, day: str) -> str:
    targets = [target.name for target in criteria.targets]
    lines = [f"# Shortlist {day}", "", _summary(selection.counts, selection.previous_day), ""]
    for (band, rooms_index), group in groupby(
        selection.candidates, key=lambda item: (item.band, item.rooms_index)
    ):
        lines += [f"## {section_label(criteria, band, rooms_index)}", ""]
        lines += [_header(targets), "|" + " --- |" * len(_columns(targets))]
        for item in group:
            marks = (NEW_MARK if item.ss_id in selection.new_ids else "") + (
                DROP_MARK if item.ss_id in selection.drop_ids else ""
            )
            lines.append(_row(item, marks, targets, item.ss_id in selection.transit))
        lines.append("")
    return "\n".join(lines)


def section_label(criteria: Criteria, band: int | None, rooms_index: int) -> str:
    return f"{band_label(criteria, band)} · {_rooms_label(criteria, rooms_index)}"


def _price_history(
    events: Iterable[Mapping[str, Any]], ss_ids: set[str]
) -> dict[str, list[tuple[str, float]]]:
    """Per listing whose price changed: the first known price, then each new price with its day."""
    history: dict[str, list[tuple[str, float]]] = {}
    for event in sorted(events, key=lambda event: str(event.get("at", ""))):
        ss_id = str(event.get("ss_id"))
        change = (event.get("changed") or {}).get("core.price_eur")
        if ss_id not in ss_ids or event.get("event") != "listing_changed" or not _is_price_pair(change):
            continue
        day = str(event.get("at", ""))[:10]
        steps = history.setdefault(ss_id, [("", change[0])])
        steps.append((day, change[1]))
    return history


def _is_price_pair(change: Any) -> TypeGuard[list[float]]:
    return (
        isinstance(change, list)
        and len(change) == 2
        and all(isinstance(price, (int, float)) for price in change)
        and change[0] != change[1]
    )


def _price_dropped(event: Mapping[str, Any]) -> bool:
    change = (event.get("changed") or {}).get("core.price_eur")
    if event.get("event") != "listing_changed" or not isinstance(change, list) or len(change) != 2:
        return False
    old, new = change
    return isinstance(old, (int, float)) and isinstance(new, (int, float)) and new < old


def _summary(counts: DigestCounts, previous_day: str | None) -> str:
    rejected = ", ".join(f"{gate} {count}" for gate, count in counts.rejected.items()) or "none"
    since = f"since {previous_day}" if previous_day else "no previous digest"
    return (
        f"Active {counts.active} · candidates {counts.candidates} · rejected: {rejected}. "
        f"{since.capitalize()}: {NEW_MARK} new {counts.new} · {DROP_MARK} cheaper "
        f"{counts.price_drops} · removed {counts.removed}."
    )


def band_label(criteria: Criteria, band: int | None) -> str:
    if band is None:
        return "price ?"
    low, high = criteria.bands[band]
    if low == 0:
        return f"<{high // 1000}k €"
    return f"{low // 1000}k–{high // 1000}k €"


def _rooms_label(criteria: Criteria, rooms_index: int) -> str:
    if rooms_index >= len(criteria.room_order):
        return "rooms ?"
    rooms = criteria.room_order[rooms_index]
    return f"{rooms} room" if rooms == 1 else f"{rooms} rooms"


def _columns(targets: list[str]) -> list[str]:
    return [*COLUMNS, *targets, "flags", "link"]


def _header(targets: list[str]) -> str:
    return "| " + " | ".join(_columns(targets)) + " |"


def _row(item: Evaluation, marks: str, targets: list[str], located: bool) -> str:
    core = item.record.get("core") or {}
    price = core.get("price_eur")
    address = " ".join(str(part) for part in (core.get("street"), core.get("house_number")) if part)
    if item.precision == "approx":
        address += f" {APPROX_MARK}"
    layout = "—" if item.layout.value == "n/a" else f"{item.layout.value} ({item.layout.source})"
    cells = [
        marks,
        f"{price:,}".replace(",", " ") if isinstance(price, (int, float)) else UNKNOWN_CELL,
        _plain(core.get("area_m2")),
        f"{item.price_ratio:.2f}" if item.price_ratio is not None else UNKNOWN_CELL,
        address or UNKNOWN_CELL,
        _plain(core.get("district")),
        f"{_plain(core.get('building_series'))} / {_plain(core.get('building_type'))}",
        f"{_plain(core.get('floor'))}/{_plain(core.get('total_floors'))}",
        wear_label(item.building),
        layout,
        *(
            ("; ".join(journey_label(option) for option in item.journeys.get(name) or []) or NO_ROUTE)
            if located
            else UNKNOWN_CELL
            for name in targets
        ),
        ", ".join(alert.text for alert in alerts_for(item)),
        f"[ss]({item.record.get('url')})",
    ]
    return "| " + " | ".join(_escape(str(cell)) for cell in cells) + " |"


def wear_label(building: Mapping[str, Any]) -> str:
    if building.get("wear"):
        surveyed = str(building.get("wear_date") or "")[:4]
        return f"{building['wear']} ({surveyed})" if surveyed else str(building["wear"])
    return str(building.get("note") or UNKNOWN_CELL)


def journey_label(option: Mapping[str, Any]) -> str:
    legs = " → ".join("/".join(leg.get("routes") or []) for leg in option.get("legs") or [])
    return f"{legs} ({option.get('minutes')} min, every {option.get('every_min')})"


def _plain(value: Any) -> str:
    return UNKNOWN_CELL if value is None or value == "" else str(value)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
