"""Pure record shaping for the JSONL listing library."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from flat_searcher.models import ListingPayload

Record = dict[str, Any]

ACTIVE = "active"
REMOVED = "removed"

SUBSTANTIVE = "substantive"
INCIDENTAL = "incidental"

UNKNOWN = "unknown"

JUDGMENT_VERSION = 1

# A median over 2-4 comparables is noise, not a baseline.
MIN_MARKET_SAMPLE = 5

SUBSTANTIVE_FIELDS = frozenset({"core.price_eur", "text.description", "images"})

_HASHED_SECTIONS = ("core", "fields", "text", "images")
_DIFFED_SECTIONS = ("core", "fields", "text", "stats")
_DIFFED_SCALARS = ("url", "status")


def build_record(
    parsed: ListingPayload,
    *,
    seen_at: str,
    previous: Record | None = None,
    floor_plan_urls: Iterable[str] = (),
    status: str = ACTIVE,
) -> Record:
    content = {
        "core": {
            "price_eur": parsed.price_eur,
            "area_m2": parsed.area_m2,
            "declared_rooms": parsed.declared_rooms_ss,
            "floor": parsed.floor,
            "total_floors": parsed.total_floors,
            "district": parsed.district,
            "street": parsed.street,
            "house_number": parsed.house_number,
            "building_series": parsed.building_series,
            "building_type": parsed.building_type,
        },
        "fields": _merge_fields(parsed.detail_fields, parsed.listing_table_metadata),
        "text": {"title": parsed.listing_title, "description": parsed.description_text},
        "images": _images(parsed.image_urls, floor_plan_urls),
    }
    known = previous or {}
    digest = content_hash(content)
    computed = price_per_m2(parsed.price_eur, parsed.area_m2)

    record: Record = {
        "ss_id": parsed.ss_id,
        "url": parsed.ss_url,
        "status": status,
        "first_seen": known.get("first_seen", seen_at),
        "last_seen": seen_at,
        "content_hash": digest,
        "revision": _revision(known, unchanged=known.get("content_hash") == digest),
        **content,
        "stats": {"unique_visits": parsed.unique_visits},
        "market": {
            "price_per_m2": computed if computed is not None else parsed.price_per_m2,
            "district_median_price_per_m2": UNKNOWN,
            "district_sample_size": 0,
        },
    }
    judgment = known.get("judgment")
    if judgment:
        record["judgment"] = judgment
    return record


def content_hash(record: Mapping[str, Any]) -> str:
    """Digest of substantive content only.

    Visit counts, timestamps, revision, market stats and judgment are excluded so that two
    crawls of an untouched listing hash identically.
    """
    payload = {section: record.get(section) for section in _HASHED_SECTIONS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def diff_records(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, list[Any]]:
    changed: dict[str, list[Any]] = {}
    for key in _DIFFED_SCALARS:
        if previous.get(key) != current.get(key):
            changed[key] = [previous.get(key), current.get(key)]
    for section in _DIFFED_SECTIONS:
        old: Mapping[str, Any] = previous.get(section) or {}
        new: Mapping[str, Any] = current.get(section) or {}
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                changed[f"{section}.{key}"] = [old.get(key), new.get(key)]
    if previous.get("images") != current.get("images"):
        changed["images"] = [previous.get("images"), current.get("images")]
    return changed


def classify_change(changed: Iterable[str]) -> str:
    return SUBSTANTIVE if any(field in SUBSTANTIVE_FIELDS for field in changed) else INCIDENTAL


def district_market_stats(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    samples: dict[str, list[float]] = {}
    for record in records:
        if record.get("status", ACTIVE) != ACTIVE:
            continue
        district, value = _district_price(record)
        if not district or value is None:
            continue
        samples.setdefault(district, []).append(value)
    return {district: _market_entry(values) for district, values in sorted(samples.items())}


def apply_market_stats(records: Iterable[Mapping[str, Any]]) -> list[Record]:
    known = [dict(record) for record in records]
    stats = district_market_stats(known)
    enriched: list[Record] = []
    for record in known:
        district, value = _district_price(record)
        entry = stats.get(district or "", _market_entry([]))
        enriched.append(
            {
                **record,
                "market": {
                    "price_per_m2": value,
                    "district_median_price_per_m2": entry["median_price_per_m2"],
                    "district_sample_size": entry["sample_size"],
                },
            }
        )
    return enriched


def merge_judgment(record: Mapping[str, Any], verdict: Mapping[str, Any]) -> Record:
    carried = {key: value for key, value in verdict.items() if key not in _VERDICT_ENVELOPE}
    judgment = {
        "version": verdict.get("version", JUDGMENT_VERSION),
        "judged_at": verdict.get("judged_at"),
        **carried,
    }
    return {**record, "judgment": judgment}


def price_per_m2(price_eur: Any, area_m2: Any) -> float | None:
    if price_eur is None or not area_m2:
        return None
    return round(float(price_eur) / float(area_m2), 2)


_VERDICT_ENVELOPE = frozenset({"ss_id", "version", "judged_at"})


def _district_price(record: Mapping[str, Any]) -> tuple[str, float | None]:
    core: Mapping[str, Any] = record.get("core") or {}
    market: Mapping[str, Any] = record.get("market") or {}
    value = market.get("price_per_m2")
    if value is None:
        value = price_per_m2(core.get("price_eur"), core.get("area_m2"))
    return str(core.get("district") or ""), None if value is None else float(value)


def _market_entry(values: list[float]) -> dict[str, Any]:
    enough = len(values) >= MIN_MARKET_SAMPLE
    return {
        "median_price_per_m2": round(float(median(values)), 2) if enough else UNKNOWN,
        "sample_size": len(values),
    }


def _revision(previous: Mapping[str, Any], *, unchanged: bool) -> int:
    current = int(previous.get("revision", 0))
    return current if unchanged and current else current + 1


def _merge_fields(*sources: Mapping[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in sources:
        for key, value in source.items():
            merged.setdefault(str(key), str(value))
    return dict(sorted(merged.items()))


def _images(urls: Iterable[str], floor_plan_urls: Iterable[str]) -> list[dict[str, Any]]:
    plans = set(floor_plan_urls)
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        images.append({"url": url, "is_floor_plan": url in plans})
    return images
