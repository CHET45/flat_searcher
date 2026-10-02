"""The verdict contract from docs/ai-instructions.md, and the parts of it that are arithmetic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VERDICT_VERSION = "v1"

YES_NO_UNKNOWN = ["yes", "no", "unknown"]

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "effective_private_rooms": {"type": ["integer", "null"]},
        "walkthrough_rooms": {"type": ["integer", "null"]},
        "kitchen_living": {"type": "string", "enum": YES_NO_UNKNOWN},
        "separate_kitchen": {"type": "string", "enum": YES_NO_UNKNOWN},
        "layout_confidence": {
            "type": "string",
            "enum": ["confirmed", "likely", "unclear", "conflict"],
        },
        "layout_source": {"type": "string", "enum": ["floor_plan", "photos", "text", "none"]},
        "rooms_conflict": {"type": "string", "enum": YES_NO_UNKNOWN},
        "layout_notes": {"type": "string", "maxLength": 400},
        "building_condition": {
            "type": "string",
            "enum": ["new", "renovated", "liveable", "needs_renovation", "unfinished", "unknown"],
        },
        "wooden_building": {"type": "string", "enum": YES_NO_UNKNOWN},
        "stove_heating": {"type": "string", "enum": YES_NO_UNKNOWN},
        "mortgage_risk": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical", "unknown"],
        },
        "mortgage_reasons": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 6},
        "price_ratio": {"type": ["number", "null"]},
        "price_verdict": {
            "type": "string",
            "enum": ["well_below", "below", "at", "above", "well_above", "unknown"],
        },
        "price_basis": {"type": "string", "enum": ["district_median", "thin_sample", "no_baseline"]},
        "suspicious_price": {"type": "string", "enum": YES_NO_UNKNOWN},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "maxLength": 1200},
    },
    "required": [
        "effective_private_rooms",
        "walkthrough_rooms",
        "kitchen_living",
        "separate_kitchen",
        "layout_confidence",
        "layout_source",
        "rooms_conflict",
        "layout_notes",
        "building_condition",
        "wooden_building",
        "stove_heating",
        "mortgage_risk",
        "mortgage_reasons",
        "price_ratio",
        "price_verdict",
        "price_basis",
        "suspicious_price",
        "score",
        "verdict",
    ],
}

JUDGMENT_KEYS = tuple(VERDICT_SCHEMA["required"])
SUSPICIOUS_RATIO = 0.65


class VerdictError(ValueError):
    pass


def price_assessment(record: Mapping[str, Any]) -> dict[str, Any]:
    """The price fields are arithmetic over `record.market`; the model only narrates them."""
    market: Mapping[str, Any] = record.get("market") or {}
    price = market.get("price_per_m2")
    median = market.get("district_median_price_per_m2")
    sample = int(market.get("district_sample_size") or 0)
    unknown = {
        "price_ratio": None,
        "price_verdict": "unknown",
        "price_basis": "no_baseline",
        "suspicious_price": "unknown",
    }
    if price is None or not sample:
        return unknown
    if not isinstance(median, (int, float)):
        return {**unknown, "price_basis": "thin_sample"}
    ratio = round(float(price) / float(median), 2)
    if ratio <= 0.75:
        verdict = "well_below"
    elif ratio <= 0.90:
        verdict = "below"
    elif ratio <= 1.15:
        verdict = "at"
    elif ratio <= 1.35:
        verdict = "above"
    else:
        verdict = "well_above"
    return {
        "price_ratio": ratio,
        "price_verdict": verdict,
        "price_basis": "district_median",
        "suspicious_price": "yes" if ratio < SUSPICIOUS_RATIO else "no",
    }


def finalize_verdict(
    raw: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    model: str,
    judged_at: str,
) -> dict[str, Any]:
    missing = [key for key in JUDGMENT_KEYS if key not in raw]
    if missing:
        raise VerdictError(f"verdict is missing {', '.join(missing)}")
    judgment = {key: raw[key] for key in JUDGMENT_KEYS}
    judgment.update(price_assessment(entry["record"]))
    score = judgment["score"]
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise VerdictError(f"score must be an integer 0-100, got {score!r}")
    for key in ("effective_private_rooms", "walkthrough_rooms"):
        value = judgment[key]
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise VerdictError(f"{key} must be an integer or null, got {value!r}")
    judgment["mortgage_reasons"] = [str(reason) for reason in judgment["mortgage_reasons"] or []]
    judgment["unknowns"] = [
        key for key in JUDGMENT_KEYS if judgment[key] is None or judgment[key] == "unknown"
    ]
    return {
        "ss_id": entry["ss_id"],
        "judged_at": judged_at,
        "model": model,
        "version": VERDICT_VERSION,
        **judgment,
    }
