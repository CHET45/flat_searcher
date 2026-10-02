"""Locate SS.com street addresses in the VZD state address register."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

EXACT = "exact"
APPROX = "approx"

STREET_TYPES = frozenset(
    {
        "iela", "gatve", "bulvāris", "prospekts", "dambis", "laukums", "līnija", "šoseja",
        "krastmala", "aleja", "šķērslīnija", "ceļš", "sēta", "mala", "šķērsiela",
    }
)
ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "g.": ("gatve",),
    "d.": ("dambis",),
    "l.": ("laukums", "līnija"),
    "lauk.": ("laukums",),
    "pr.": ("prospekts",),
    "b.": ("bulvāris",),
    "bulv.": ("bulvāris",),
    "al.": ("aleja",),
    "šķ.": ("šķērslīnija",),
    "krastm.": ("krastmala",),
    "kr.": ("krastmala",),
}
DEFAULT_TYPE = "iela"

_HOUSE = r"\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?(?: k-\d+)?"
_REGISTER_ADDRESS = re.compile(rf"^(.+?) ({_HOUSE})$")
_TRAILING_HOUSE = re.compile(r"^(.+?)\s+(\d+\s*[a-z]?(?:\s*[-‐]?\s*[kк]\s*-?\s*\d+)?)$", re.IGNORECASE)
_BLOCK = re.compile(r"^(\d+[a-z]?)[kк]-?(\d+)$")
_FLAT_SUFFIX = re.compile(r"^(\d+[a-z]?)-\d+$")
_NUMBER = re.compile(r"\d+")
_DANGLING_BLOCK = re.compile(r"^(.+?)\s+(\d+[a-z]?)\s*[kк]$", re.IGNORECASE)

StreetKey = tuple[tuple[str, ...], str | None]


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    precision: str
    matched: str


@dataclass(frozen=True)
class _Building:
    house: str
    lat: float
    lon: float
    address: str


def parse_register_rows(rows: Iterable[Mapping[str, str]]) -> list[tuple[str, float, float]]:
    buildings: list[tuple[str, float, float]] = []
    for row in rows:
        if row.get("STATUSS") != "EKS":
            continue
        parts = (row.get("STD") or "").split(", ")
        if len(parts) < 2 or parts[1] != "Rīga":
            continue
        try:
            buildings.append((parts[0], float(row["DD_N"]), float(row["DD_E"])))
        except (KeyError, ValueError):
            continue
    return buildings


class AddressIndex:
    def __init__(self, entries: Iterable[tuple[str, float, float]]) -> None:
        self._streets: dict[StreetKey, list[_Building]] = defaultdict(list)
        self._by_last_word: dict[str, set[StreetKey]] = defaultdict(set)
        for address, lat, lon in entries:
            match = _REGISTER_ADDRESS.match(address)
            if not match:
                continue
            words = match.group(1).split()
            kind = words[-1].casefold() if words[-1].casefold() in STREET_TYPES else None
            core = tuple(_fold(word) for word in (words[:-1] if kind else words))
            if not core:
                continue
            key = (core, kind)
            self._streets[key].append(_Building(_house_key(match.group(2)), lat, lon, address))
            self._by_last_word[core[-1]].add(key)

    def locate_text(self, text: str) -> Location | None:
        head = text.split(",")[0].strip()
        match = _TRAILING_HOUSE.match(head)
        if not match:
            return None
        return self.locate(match.group(1), match.group(2))

    def locate(self, street: str, house: str | None) -> Location | None:
        street = " ".join(street.replace("šķ l.", "šķērslīnija").split()).rstrip(",;")
        dangling = _DANGLING_BLOCK.match(street)
        if dangling and house and house.strip().isdigit():
            street, house = dangling.group(1), f"{dangling.group(2)}k{house.strip()}"
        if not house:
            embedded = _TRAILING_HOUSE.match(street)
            if embedded and not embedded.group(1).endswith("."):
                street, house = embedded.group(1), embedded.group(2)
        words = street.split()
        if not words:
            return None
        kinds, explicit = (DEFAULT_TYPE,), False
        if words[-1].casefold() in STREET_TYPES:
            kinds, explicit = (words[-1].casefold(),), True
            words = words[:-1]
        elif words[-1].casefold() in ABBREVIATIONS:
            kinds, explicit = ABBREVIATIONS[words[-1].casefold()], True
            words = words[:-1]
        core = [_fold(word) for word in words]
        if not core:
            return None

        pools = self._pools(core, kinds, explicit)
        if not pools:
            return None
        if house:
            wanted = _house_key(house)
            for pool in pools:
                hits = {key: found for key in pool if (found := self._find_house(key, wanted))}
                if len(hits) == 1:
                    building = next(iter(hits.values()))
                    return Location(building.lat, building.lon, EXACT, building.address)
                if len(hits) > 1:
                    return None
        if len(pools[0]) != 1:
            return None
        return self._nearest(pools[0][0], house)

    def _pools(self, core: list[str], kinds: tuple[str, ...], explicit: bool) -> list[list[StreetKey]]:
        matching = [key for key in self._by_last_word.get(core[-1], ()) if _tail_matches(core, key[0])]
        typed = [key for key in matching if key[1] in kinds]
        other = [] if explicit else [key for key in matching if key[1] not in kinds]
        pools: list[list[StreetKey]] = []
        for group in (typed, other):
            same_length = sorted(key for key in group if len(key[0]) == len(core))
            longer = sorted(key for key in group if len(key[0]) != len(core))
            pools.extend(pool for pool in (same_length, longer) if pool)
        return pools

    def _find_house(self, key: StreetKey, wanted: str) -> _Building | None:
        buildings = self._streets[key]
        for building in buildings:
            if building.house == wanted:
                return building
        for building in buildings:
            if _loose_house(building.house) == _loose_house(wanted) or (
                wanted in building.house.split("/")
            ):
                return building
        return None

    def _nearest(self, key: StreetKey, house: str | None) -> Location:
        buildings = self._streets[key]
        number = _NUMBER.match(_house_key(house)) if house else None
        if number is None:
            chosen = sorted(buildings, key=lambda building: (building.lat, building.lon))[
                len(buildings) // 2
            ]
        else:
            target = int(number.group())
            chosen = min(buildings, key=lambda building: abs(_number(building.house) - target))
        return Location(chosen.lat, chosen.lon, APPROX, chosen.address)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()


def _tail_matches(core: list[str], register: tuple[str, ...]) -> bool:
    if len(core) > len(register):
        return False
    tail = register[len(register) - len(core) :]
    return all(
        given == known or (given.endswith(".") and len(given) > 1 and known.startswith(given[:-1]))
        for given, known in zip(core, tail)
    )


def _house_key(house: str) -> str:
    compact = _fold(house.replace("к", "k")).replace(" ", "")
    block = _BLOCK.match(compact)
    if block:
        return f"{block.group(1)}k-{block.group(2)}"
    flat = _FLAT_SUFFIX.match(compact)
    return flat.group(1) if flat else compact


def _loose_house(house: str) -> str:
    return house.split("k-")[0].split("/")[0]


def _number(house: str) -> int:
    match = _NUMBER.match(house)
    return int(match.group()) if match else 0
