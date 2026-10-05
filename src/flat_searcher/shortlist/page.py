"""The digest as a small page, with its map data and photos published next to it."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

from flat_searcher.shortlist.criteria import Criteria, TodayRules
from flat_searcher.shortlist.alerts import alerts_for
from flat_searcher.shortlist.digest import Selection, band_label, section_label
from flat_searcher.shortlist.facts import Fact
from flat_searcher.shortlist.groups import group_ads
from flat_searcher.shortlist.rank import Evaluation, expected_minutes
from flat_searcher.shortlist.today import pick_today

TEMPLATE = "page.html"
DATA_MARK = "__DATA__"
MAP_SCRIPT = "map.js"
MAP_GLOBAL = "flatMapData"
PHOTO_DIR = "photos"
SORT_METRICS = {"time": "expected", "transfers": "transfers", "every": "every_min", "walk": "walk_m"}
ALL_TARGETS = "*"
SOURCE_FIELDS = ("Cena", "Platība", "Istabas", "Stāvs", "Sērija", "Mājas tips", "Iela")


@dataclass(frozen=True)
class PageFiles:
    html: str
    map_script: str
    photo_ids: tuple[str, ...]
    flats: int


def render_page(
    selection: Selection,
    criteria: Criteria,
    day: str,
    photos: Collection[str],
    targets: Mapping[str, Mapping[str, Any]],
    transit_map: Mapping[str, Any],
) -> PageFiles:
    names = [target.name for target in criteria.targets]
    groups = group_ads(selection.candidates)
    today = {
        pick.ss_id: pick.front
        for pick in pick_today([group[0] for group in groups], criteria.today, names)
    }
    cards: list[dict[str, Any]] = []
    journeys: dict[str, dict[str, list[dict[str, Any]]]] = {}
    photo_ids: list[str] = []
    for group in groups:
        card = _card(group, criteria, selection, names, today)
        photo = next((item.ss_id for item in group if item.ss_id in photos), None)
        if photo:
            card["photo"] = f"{PHOTO_DIR}/{photo}.jpg"
            photo_ids.append(photo)
        if card["best"]:
            journeys[card["id"]] = {name: list(group[0].journeys.get(name) or []) for name in names}
        cards.append(card)
    stops = transit_map.get("stops") or {}
    data = {
        "day": day,
        "previousDay": selection.previous_day,
        "counts": {**asdict(selection.counts), "flats": len(groups)},
        "targetNames": names,
        "targets": [
            {"name": name, "lat": targets[name]["lat"], "lon": targets[name]["lon"]}
            for name in names
            if name in targets
        ],
        "bands": [band_label(criteria, index) for index in range(len(criteria.bands))],
        "rooms": list(criteria.room_order),
        "today": {"size": criteria.today.size, "rules": today_rules(criteria.today)},
        "cards": cards,
        "stops": {stop: stops[stop] for stop in sorted(_card_stops(cards)) if stop in stops},
        "transit": _transit_meta(selection.transit),
        "mapScript": f"{MAP_SCRIPT}?v={day}",
    }
    map_data = {
        "journeys": journeys,
        "shapes": transit_map.get("shapes") or {},
        "stops": stops,
        "streets": transit_map.get("streets") or {},
    }
    template = resources.files("flat_searcher.shortlist").joinpath(TEMPLATE).read_text("utf-8")
    return PageFiles(
        html=template.replace(DATA_MARK, _json(data)),
        map_script=f"window.{MAP_GLOBAL} = {_json(map_data)};\n",
        photo_ids=tuple(photo_ids),
        flats=len(groups),
    )


def today_rules(rules: TodayRules) -> list[str]:
    words = []
    if rules.exclude_walkthrough:
        words.append("no walk-through room")
    if rules.exclude_leased_land:
        words.append("land not leased")
    if rules.exclude_illegal_replanning:
        words.append("no replanning the seller calls not legalised")
    words.append("no implausible price, not room-sized")
    if rules.max_minutes is not None:
        words.append(f"at most {rules.max_minutes} min to all targets together, waits included")
    else:
        words.append("a way to every target")
    return words


def _json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def _transit_meta(transit: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    entry = next(iter(transit.values()), {})
    return {"day": entry.get("day"), "window": entry.get("window")}


def _card(
    group: Sequence[Evaluation],
    criteria: Criteria,
    selection: Selection,
    names: list[str],
    today: Mapping[str, int],
) -> dict[str, Any]:
    item = group[0]
    record = item.record
    core = record.get("core") or {}
    fields = record.get("fields") or {}
    entry = selection.transit.get(item.ss_id) or {}
    layout_quote = item.layout.evidence[0] if item.layout.source == "text" and item.layout.evidence else None
    first_seen = min(str(ad.record.get("first_seen") or "")[:10] for ad in group)
    return {
        "id": item.ss_id,
        "url": record.get("url"),
        "ads": [
            {"id": ad.ss_id, "url": ad.record.get("url"), "price": (ad.record.get("core") or {}).get("price_eur")}
            for ad in group[1:]
        ],
        "section": section_label(criteria, item.band, item.rooms_index),
        "band": item.band,
        "rooms": core.get("declared_rooms"),
        "new": all(ad.ss_id in selection.new_ids for ad in group),
        "cheaper": any(ad.ss_id in selection.drop_ids for ad in group),
        "firstSeen": first_seen or None,
        "price": core.get("price_eur"),
        "area": core.get("area_m2"),
        "ratio": item.price_ratio,
        "address": " ".join(
            str(part) for part in (core.get("street"), core.get("house_number")) if part
        ),
        "approx": item.precision == "approx",
        "lat": entry.get("lat"),
        "lon": entry.get("lon"),
        "district": core.get("district"),
        "series": core.get("building_series"),
        "type": core.get("building_type"),
        "floor": core.get("floor"),
        "floors": core.get("total_floors"),
        "layout": {"value": item.layout.value, "source": item.layout.source, "quote": layout_quote},
        "land": {"value": item.land.value, "quote": item.land.evidence[0] if item.land.evidence else None},
        "company": fields.get("Uzņēmums") or None,
        "model": _model(record),
        "history": [[day, price] for day, price in selection.price_history.get(item.ss_id, [])],
        "best": {name: _best(item.journeys.get(name) or []) for name in names} if entry else {},
        "sort": _sort_keys(item.journeys, names) if entry else {},
        "reached": item.reached,
        "alerts": [asdict(alert) for alert in alerts_for(item)],
        "heating": _fact(item.heating),
        "hotWater": _fact(item.hot_water),
        "replanning": _fact(item.replanning),
        "source": {key: fields[key] for key in SOURCE_FIELDS if fields.get(key)},
        "building": dict(item.building),
        "today": today.get(item.ss_id),
        "photo": None,
    }


def _best(options: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not options:
        return None
    option = min(options, key=expected_minutes)
    return {
        "minutes": option["minutes"],
        "every_min": option["every_min"],
        "transfers": option["transfers"],
        "walk_m": option["walk_m"],
        "legs": [
            {key: leg[key] for key in ("routes", "board", "alight", "every_min")}
            for leg in option.get("legs") or []
        ],
    }


def _card_stops(cards: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        stop
        for card in cards
        for option in card["best"].values()
        if option
        for leg in option["legs"]
        for stop in (leg["board"], leg["alight"])
    }


def _fact(fact: Fact) -> dict[str, Any]:
    return {"value": fact.value, "quote": fact.evidence[0] if fact.evidence else None}


def _model(record: Mapping[str, Any]) -> dict[str, Any] | None:
    judgment = record.get("judgment") or {}
    if not judgment:
        return None
    return {
        "condition": judgment.get("building_condition"),
        "mortgage": judgment.get("mortgage_risk"),
        "reasons": list(judgment.get("mortgage_reasons") or []),
    }


def _metric(option: Mapping[str, Any], field: str) -> float:
    return expected_minutes(option) if field == "expected" else option[field]


def _sort_keys(
    journeys: Mapping[str, Any], names: list[str]
) -> dict[str, dict[str, list[float] | None]]:
    """Per metric and target: [metric, minutes] of the option best on that metric."""
    keys: dict[str, dict[str, list[float] | None]] = {}
    for metric, field in SORT_METRICS.items():
        per_target: dict[str, list[float] | None] = {}
        for name in names:
            options = journeys.get(name) or []
            per_target[name] = (
                list(min((_metric(option, field), option["minutes"]) for option in options))
                if options
                else None
            )
        chosen = [key for key in per_target.values() if key is not None]
        combine = max if metric == "every" else sum
        per_target[ALL_TARGETS] = (
            [combine(key[0] for key in chosen), sum(key[1] for key in chosen)]
            if chosen and len(chosen) == len(names)
            else None
        )
        keys[metric] = per_target
    return keys
