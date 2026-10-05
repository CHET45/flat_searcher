"""Shortlist criteria, read from `criteria.toml` in the library."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_WALK_M = 500
DEFAULT_TRANSFER_WALK_M = 300
DEFAULT_WINDOW = ("07:00", "10:00")
DEFAULT_MAX_TRANSFERS = 2
DEFAULT_JOURNEYS_MAX = 4

_SECTIONS: dict[str, frozenset[str]] = {
    "price": frozenset({"max_eur", "bands"}),
    "rooms": frozenset({"order"}),
    "floor": frozenset({"exclude"}),
    "surroundings": frozenset({"gym_walk_max_min"}),
    "building": frozenset({"excluded_types"}),
    "heating": frozenset({"exclude_stove"}),
    "transit": frozenset(
        {
            "walk_m",
            "transfer_walk_m",
            "window_start",
            "window_end",
            "max_transfers",
            "journeys_max",
            "targets",
        }
    ),
    "today": frozenset(
        {"size", "max_minutes", "exclude_walkthrough", "exclude_leased_land", "exclude_illegal_replanning"}
    ),
}
_TARGET_KEYS = frozenset({"name", "address", "lat", "lon"})


class CriteriaError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    address: str | None
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class TodayRules:
    """Soft rules of the short daily view; the full list keeps every candidate."""

    size: int = 20
    max_minutes: int | None = None
    exclude_walkthrough: bool = True
    exclude_leased_land: bool = True
    exclude_illegal_replanning: bool = True


@dataclass(frozen=True)
class Criteria:
    max_eur: int
    bands: tuple[tuple[int, int], ...]
    room_order: tuple[int, ...]
    excluded_types: frozenset[str]
    exclude_stove: bool
    walk_m: int
    transfer_walk_m: int
    window: tuple[str, str]
    max_transfers: int
    journeys_max: int
    targets: tuple[Target, ...]
    today: TodayRules = TodayRules()
    excluded_floors: frozenset[int] = frozenset()
    gym_walk_max_min: int | None = None

    def band_index(self, price: float) -> int | None:
        if price > self.max_eur:
            return None
        for index, (low, high) in enumerate(self.bands):
            if low <= price < high:
                return index
        for index, (_, high) in enumerate(self.bands):
            if price == high == self.max_eur:
                return index
        return None


def parse_criteria(text: str) -> Criteria:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CriteriaError(f"criteria.toml is not valid TOML: {error}") from error
    _reject_unknown(data.keys(), frozenset(_SECTIONS), "")
    for section, allowed in _SECTIONS.items():
        _reject_unknown(_section(data, section).keys(), allowed, f"{section}.")

    price = _section(data, "price")
    if not isinstance(price.get("max_eur"), int):
        raise CriteriaError("price.max_eur must be an integer")
    max_eur = price["max_eur"]
    bands = tuple(_band(band) for band in price.get("bands", [[0, max_eur]]))
    transit = _section(data, "transit")
    return Criteria(
        max_eur=max_eur,
        bands=bands,
        room_order=tuple(int(rooms) for rooms in _section(data, "rooms").get("order", [])),
        excluded_types=frozenset(
            str(kind) for kind in _section(data, "building").get("excluded_types", [])
        ),
        exclude_stove=bool(_section(data, "heating").get("exclude_stove", False)),
        walk_m=int(transit.get("walk_m", DEFAULT_WALK_M)),
        transfer_walk_m=int(transit.get("transfer_walk_m", DEFAULT_TRANSFER_WALK_M)),
        window=(
            str(transit.get("window_start", DEFAULT_WINDOW[0])),
            str(transit.get("window_end", DEFAULT_WINDOW[1])),
        ),
        max_transfers=int(transit.get("max_transfers", DEFAULT_MAX_TRANSFERS)),
        journeys_max=int(transit.get("journeys_max", DEFAULT_JOURNEYS_MAX)),
        targets=tuple(_target(target) for target in transit.get("targets", [])),
        today=_today(_section(data, "today")),
        excluded_floors=_floors(_section(data, "floor").get("exclude", [])),
        gym_walk_max_min=_minutes(_section(data, "surroundings").get("gym_walk_max_min"), "surroundings.gym_walk_max_min"),
    )


def target_definition(target: Target) -> dict[str, Any]:
    return {"address": target.address, "lat": target.lat, "lon": target.lon}


def changed_targets(criteria: Criteria, computed: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Targets whose journeys the last transit run did not compute as they are defined now."""
    changed = []
    for target in criteria.targets:
        point = computed.get(target.name)
        definition = target_definition(target)
        if point is None or point.get("defined_as", definition) != definition:
            changed.append(target.name)
    return changed


def _today(section: Mapping[str, Any]) -> TodayRules:
    defaults = TodayRules()
    max_minutes = section.get("max_minutes")
    if max_minutes is not None and not isinstance(max_minutes, int):
        raise CriteriaError("today.max_minutes must be an integer")
    return TodayRules(
        size=int(section.get("size", defaults.size)),
        max_minutes=max_minutes,
        exclude_walkthrough=bool(section.get("exclude_walkthrough", defaults.exclude_walkthrough)),
        exclude_leased_land=bool(section.get("exclude_leased_land", defaults.exclude_leased_land)),
        exclude_illegal_replanning=bool(
            section.get("exclude_illegal_replanning", defaults.exclude_illegal_replanning)
        ),
    )


def _minutes(value: Any, key: str) -> int | None:
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise CriteriaError(f"{key} must be a positive integer")
    return value


def _floors(floors: Any) -> frozenset[int]:
    if not isinstance(floors, list) or not all(isinstance(floor, int) for floor in floors):
        raise CriteriaError("floor.exclude must be a list of integers")
    return frozenset(floors)


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = data.get(name, {})
    if not isinstance(section, Mapping):
        raise CriteriaError(f"{name} must be a table")
    return section


def _reject_unknown(keys: Any, allowed: frozenset[str], prefix: str) -> None:
    unknown = sorted(set(keys) - allowed)
    if unknown:
        raise CriteriaError(f"unknown key {prefix}{unknown[0]}")


def _band(band: Any) -> tuple[int, int]:
    if (
        not isinstance(band, list)
        or len(band) != 2
        or not all(isinstance(bound, int) for bound in band)
        or band[0] >= band[1]
    ):
        raise CriteriaError(f"price.bands entries must be [low, high] with low < high: {band!r}")
    return band[0], band[1]


def _target(target: Any) -> Target:
    if not isinstance(target, Mapping) or not isinstance(target.get("name"), str):
        raise CriteriaError("every transit.targets entry needs a name")
    _reject_unknown(target.keys(), _TARGET_KEYS, "transit.targets.")
    name = target["name"]
    address = target.get("address")
    lat, lon = target.get("lat"), target.get("lon")
    point = (
        (float(lat), float(lon))
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        else None
    )
    if not isinstance(address, str) and point is None:
        raise CriteriaError(f"target {name} needs an address or both lat and lon")
    return Target(
        name=name,
        address=address if isinstance(address, str) else None,
        lat=point[0] if point else None,
        lon=point[1] if point else None,
    )
