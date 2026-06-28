"""JSON-ready view payloads for the web desktop UI.

These builders translate domain read models into the compact, semantically
tagged structures the single-page frontend renders. All numeric formatting and
flag classification lives here so the JavaScript layer stays presentational.
"""

from __future__ import annotations

from collections.abc import Callable

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.db.read_models import ListingDetailReadModel
from flat_searcher.filtering import ListingCandidate
from flat_searcher.presentation import (
    comparison_flags,
    format_ai_room_label,
    key_flags,
)
from flat_searcher.ranking import RankedCandidate
from flat_searcher.scoring import (
    BLOCK_LABELS,
    ScoreBlockKey,
    mortgage_suitability_score,
)
from flat_searcher.scoring.profiles import ScoringProfile

Tr = Callable[[str], str]


def _identity(text: str) -> str:
    return text


_RISK_FLAGS = frozenset(
    {
        "Room conflict",
        "High mortgage risk",
        "Stove heating",
        "Stove heating risk",
        "Wooden building risk",
        "Suspiciously low price - check carefully",
    }
)

_GOOD_FLAGS = frozenset(
    {
        "Good price",
        "Layout confirmed",
        "Layout confirmed by floor plan",
        "Strong transport",
    }
)

_AI_FLAGS = frozenset({"Floor plan", "Kitchen-living", "Layout unclear"})


def flag_chip(label: str, tr: Tr) -> dict[str, str]:
    if label in _RISK_FLAGS:
        kind = "risk"
    elif label in _GOOD_FLAGS:
        kind = "good"
    elif label in _AI_FLAGS:
        kind = "ai"
    else:
        kind = "neutral"
    return {"label": tr(label), "type": kind}


def ranking_rows_payload(
    ranked: tuple[RankedCandidate, ...],
    tr: Tr = _identity,
) -> list[dict[str, object]]:
    return [_ranking_row(item, tr) for item in ranked]


def _ranking_row(item: RankedCandidate, tr: Tr) -> dict[str, object]:
    candidate = item.candidate
    score = _score_int(candidate.score)
    return {
        "listingId": candidate.listing_id,
        "position": item.position,
        "score": score,
        "scoreBucket": _score_bucket(candidate.score),
        "district": candidate.district or tr("Unknown district"),
        "address": candidate.street or tr("Unknown street"),
        "aiRooms": _ai_rooms_label(candidate),
        "aiRoomsType": _ai_rooms_type(candidate),
        "ssRooms": _value(candidate.declared_rooms_ss),
        "area": _area_text(candidate.area_m2),
        "areaValue": candidate.area_m2,
        "price": _money(candidate.price_eur),
        "priceValue": candidate.price_eur,
        "flags": [flag_chip(label, tr) for label in key_flags(candidate)],
        "isFavorite": candidate.is_favorite,
        "isRejected": candidate.is_rejected,
    }


def summary_payload(
    ranked: tuple[RankedCandidate, ...],
    total: int,
    tr: Tr = _identity,
) -> dict[str, object]:
    candidates = [item.candidate for item in ranked]
    scores = [c.score for c in candidates if c.score is not None]
    prices = [c.price_eur for c in candidates if c.price_eur is not None]
    areas = [c.area_m2 for c in candidates if c.area_m2 is not None]
    high_risk = sum(
        1
        for c in candidates
        if c.mortgage_risk_level in {MortgageRiskLevel.HIGH, MortgageRiskLevel.CRITICAL}
    )
    return {
        "shown": len(ranked),
        "total": total,
        "topScore": round(max(scores)) if scores else None,
        "avgPrice": _compact_eur(sum(prices) / len(prices)) if prices else None,
        "avgArea": round(sum(areas) / len(areas), 1) if areas else None,
        "highRisk": high_risk,
    }


def map_markers_payload(
    markers: tuple,
    rows_by_id: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for marker in markers:
        row = rows_by_id.get(marker.listing_id, {})
        payload.append(
            {
                "listingId": marker.listing_id,
                "latitude": marker.latitude,
                "longitude": marker.longitude,
                "visualState": marker.visual_state.value,
                "scoreBucket": marker.score_bucket,
                "score": row.get("score"),
                "price": row.get("price"),
                "district": row.get("district"),
                "address": row.get("address"),
                "aiRooms": row.get("aiRooms"),
                "ssRooms": row.get("ssRooms"),
                "isFavorite": row.get("isFavorite", False),
            }
        )
    return payload


def reference_points_payload(
    points: tuple,
    tr: Tr = _identity,
) -> list[dict[str, object]]:
    return [
        {
            "id": point.point_id,
            "latitude": point.latitude,
            "longitude": point.longitude,
            "kind": point.kind,
            "title": tr(point.title),
        }
        for point in points
    ]


def detail_payload(
    detail: ListingDetailReadModel,
    profile_name: str,
    floor_plan_data_uri: str | None,
    tr: Tr = _identity,
) -> dict[str, object]:
    return {
        "listingId": detail.listing_id,
        "district": detail.district or tr("Unknown district"),
        "address": detail.street or tr("Unknown street"),
        "houseNumber": detail.house_number,
        "ssUrl": detail.ss_url,
        "price": _money(detail.price_eur),
        "area": _area_text(detail.area_m2),
        "pricePerM2": _eur_per_m2(detail.price_per_m2),
        "overallScore": _score_int(detail.overall_score),
        "scoreBucket": _score_bucket(detail.overall_score),
        "profileName": profile_name,
        "badges": [flag_chip(label, tr) for label in _detail_badges(detail)],
        "evaluation": _evaluation(detail, tr),
        "breakdown": _breakdown(detail, tr),
        "aiInsight": _ai_insight(detail),
        "layout": _layout(detail, tr),
        "floorPlan": floor_plan_data_uri,
        "mortgage": _mortgage(detail, tr),
        "proximity": _proximity(detail, tr),
        "history": _history(detail, tr),
        "sourceText": detail.description_text or "",
        "notes": detail.user_notes or "",
        "isFavorite": detail.is_favorite,
        "isRejected": detail.is_rejected,
        "buildingSeries": detail.building_series or detail.building_type or tr("unknown"),
    }


def comparison_payload(
    details: tuple[ListingDetailReadModel, ...],
    tr: Tr = _identity,
) -> dict[str, object]:
    columns = [
        {
            "listingId": detail.listing_id,
            "district": detail.district or tr("Unknown district"),
            "address": detail.street or tr("Unknown street"),
        }
        for detail in details
    ]
    rows = [
        _comparison_row(tr("Price"), [_money(d.price_eur) for d in details]),
        _comparison_row(tr("EUR/m2"), [_eur_per_m2(d.price_per_m2) for d in details]),
        _comparison_row(tr("Area"), [_area_text(d.area_m2) for d in details]),
        _comparison_score_row(tr("Score"), [d.overall_score for d in details]),
        _comparison_rooms_row(tr("AI-effective rooms"), details, tr),
        _comparison_mortgage_row(tr("Mortgage risk"), details, tr),
        _comparison_row(
            tr("Building / series"),
            [d.building_series or d.building_type or tr("unknown") for d in details],
        ),
        _comparison_row(
            tr("Flags"),
            [", ".join(tr(f) for f in comparison_flags(d)) or tr("none") for d in details],
        ),
    ]
    return {"columns": columns, "rows": rows}


def profile_editor_payload(
    profile: ScoringProfile,
    tr: Tr = _identity,
) -> dict[str, object]:
    blocks = [
        {
            "key": block.value,
            "label": tr(BLOCK_LABELS[block]),
            "importance": profile.block_importance.get(block).value
            if profile.block_importance.get(block)
            else "Ignore",
        }
        for block in ScoreBlockKey
    ]
    return {
        "key": profile.key,
        "name": profile.name,
        "isBuiltin": profile.is_builtin,
        "blocks": blocks,
    }


def _detail_badges(detail: ListingDetailReadModel) -> list[str]:
    badges: list[str] = []
    if detail.price_value_score is not None and detail.price_value_score >= 80:
        badges.append("Good price")
    if (detail.transport_score or 0) >= 70:
        badges.append("Strong transport")
    if detail.layout_confidence_label == LayoutConfidenceLabel.CONFIRMED:
        badges.append("Layout confirmed")
    if detail.is_favorite:
        badges.append("Favorite")
    if detail.ss_vs_ai_room_conflict:
        badges.append("Room conflict")
    if detail.mortgage_risk_level in {MortgageRiskLevel.HIGH, MortgageRiskLevel.CRITICAL}:
        badges.append("High mortgage risk")
    return badges


def _evaluation(detail: ListingDetailReadModel, tr: Tr) -> list[dict[str, object]]:
    location_scores = [
        score
        for score in (
            detail.rtu_score,
            detail.transport_score,
            detail.station_score,
            detail.shop_score,
        )
        if score is not None
    ]
    location_value = round(sum(location_scores) / len(location_scores)) if location_scores else None
    mortgage_value = (
        None
        if detail.mortgage_risk_level is None
        else round(mortgage_suitability_score(detail.mortgage_risk_level))
    )
    rows = [
        (tr("Price to value"), _score_int(detail.price_value_score)),
        (tr("Location viability"), location_value),
        (tr("Mortgage suitability"), mortgage_value),
    ]
    return [
        {"label": label, "value": value, "bucket": _bucket_from_int(value)} for label, value in rows
    ]


def _breakdown(detail: ListingDetailReadModel, tr: Tr) -> list[dict[str, object]]:
    import json

    if not detail.score_breakdown_json:
        return []
    try:
        raw = json.loads(detail.score_breakdown_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
    items: list[dict[str, object]] = []
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        score = value.get("score")
        if not isinstance(score, (int, float)):
            continue
        items.append(
            {
                "label": tr(_block_label(str(key))),
                "value": round(score),
                "bucket": _bucket_from_int(round(score)),
            }
        )
    items.sort(key=lambda item: abs(int(item["value"]) - 50), reverse=True)
    return items[:5]


def _ai_insight(detail: ListingDetailReadModel) -> str:
    for candidate in (
        detail.score_explanation,
        detail.layout_explanation_user,
        detail.market_baseline_explanation,
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


def _layout(detail: ListingDetailReadModel, tr: Tr) -> dict[str, object]:
    confidence = (
        tr("Unknown")
        if detail.layout_confidence_label is None
        else tr(detail.layout_confidence_label.value)
    )
    return {
        "aiRooms": _value(detail.effective_private_rooms),
        "ssRooms": _value(detail.declared_rooms_ss),
        "walkthrough": _value(detail.walkthrough_rooms),
        "kitchenLiving": detail.kitchen_living_detected,
        "confidence": confidence,
        "explanation": detail.layout_explanation_user or tr("not analyzed yet"),
    }


def _mortgage(detail: ListingDetailReadModel, tr: Tr) -> dict[str, object]:
    level = detail.mortgage_risk_level
    if level is None:
        return {"level": tr("Unknown"), "type": "neutral", "text": tr("not analyzed yet")}
    type_by_level = {
        MortgageRiskLevel.LOW: "risk-low",
        MortgageRiskLevel.MEDIUM: "risk-medium",
        MortgageRiskLevel.HIGH: "risk-high",
        MortgageRiskLevel.CRITICAL: "risk-high",
    }
    text = (
        detail.mortgage_explanation_user or detail.mortgage_risk_reasons or tr("not analyzed yet")
    )
    return {
        "level": tr(level.value),
        "type": type_by_level.get(level, "neutral"),
        "text": text,
    }


def _proximity(detail: ListingDetailReadModel, tr: Tr) -> list[dict[str, object]]:
    return [
        {
            "icon": "school",
            "label": tr("RTU Campus"),
            "value": _distance(detail.distance_to_rtu_m),
        },
        {
            "icon": "directions_transit",
            "label": tr("Central station"),
            "value": _distance(detail.distance_to_central_station_m),
        },
        {
            "icon": "shopping_cart",
            "label": tr("Grocery shops"),
            "value": _count_within(detail.shops_within_1200m, 1200, tr),
        },
        {
            "icon": "directions_bus",
            "label": tr("Public transport"),
            "value": _count_within(detail.transport_stops_nearby_count, 900, tr),
        },
    ]


def _history(detail: ListingDetailReadModel, tr: Tr) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    trend = _price_trend(detail)
    if trend is not None:
        rows.append(trend)
    if detail.history_snapshots:
        latest = detail.history_snapshots[0]
        rows.append(
            {
                "icon": "schedule",
                "label": tr("Last checked"),
                "value": _short_timestamp(latest.checked_at),
                "type": "neutral",
            }
        )
    rows.append(
        {
            "icon": "history",
            "label": tr("Snapshots"),
            "value": str(len(detail.history_snapshots)),
            "type": "neutral",
        }
    )
    return rows


def _price_trend(detail: ListingDetailReadModel) -> dict[str, object] | None:
    prices = [s.price_eur for s in detail.history_snapshots if s.price_eur is not None]
    if len(prices) < 2:
        return None
    latest, oldest = prices[0], prices[-1]
    if oldest <= 0 or latest == oldest:
        return None
    change = (latest - oldest) / oldest * 100
    dropped = change < 0
    return {
        "icon": "trending_down" if dropped else "trending_up",
        "label": "Price change",
        "value": f"{'-' if dropped else '+'}{abs(change):.0f}%",
        "type": "good" if dropped else "risk",
    }


def _comparison_row(label: str, values: list[str]) -> dict[str, object]:
    return {"label": label, "values": [{"text": value} for value in values]}


def _comparison_score_row(label: str, scores: list) -> dict[str, object]:
    return {
        "label": label,
        "kind": "score",
        "values": [
            {
                "text": str(_score_int(score)) if score is not None else "—",
                "value": _score_int(score),
                "bucket": _score_bucket(score),
            }
            for score in scores
        ],
    }


def _comparison_rooms_row(label, details, tr) -> dict[str, object]:
    return {
        "label": label,
        "values": [{"text": _ai_rooms_label_detail(d, tr)} for d in details],
    }


def _comparison_mortgage_row(label, details, tr) -> dict[str, object]:
    values = []
    for d in details:
        mortgage = _mortgage(d, tr)
        values.append({"text": mortgage["level"], "type": mortgage["type"]})
    return {"label": label, "kind": "badge", "values": values}


def _ai_rooms_label(candidate: ListingCandidate) -> str:
    if candidate.effective_private_rooms is None:
        return "—"
    return (
        format_ai_room_label(candidate.effective_private_rooms, candidate.kitchen_living_detected)
        .replace(" private", "")
        .replace(" + kitchen-living", "+K")
    )


def _ai_rooms_label_detail(detail: ListingDetailReadModel, tr: Tr) -> str:
    if detail.effective_private_rooms is None:
        return tr("unclear")
    return tr(format_ai_room_label(detail.effective_private_rooms, detail.kitchen_living_detected))


def _ai_rooms_type(candidate: ListingCandidate) -> str:
    if candidate.room_conflict:
        return "mismatch"
    if candidate.kitchen_living_detected:
        return "neutral"
    if candidate.effective_private_rooms is None:
        return "neutral"
    return "ai"


def _block_label(raw_key: str) -> str:
    try:
        return BLOCK_LABELS[ScoreBlockKey(raw_key)]
    except (KeyError, ValueError):
        return raw_key.replace("_", " ").title()


def _score_int(score: float | None) -> int | None:
    return None if score is None else round(score)


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "low"
    return "very_low"


def _bucket_from_int(value: int | None) -> str:
    return _score_bucket(None if value is None else float(value))


def _money(price_eur: int | None) -> str:
    if price_eur is None:
        return "—"
    return f"{price_eur:,}".replace(",", " ") + " €"


def _compact_eur(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def _eur_per_m2(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " €/m²"


def _area_text(area_m2: float | None) -> str:
    if area_m2 is None:
        return "—"
    value = float(area_m2)
    return f"{int(value)} m²" if value.is_integer() else f"{value:.1f} m²"


def _distance(distance_m: float | None) -> str:
    if distance_m is None:
        return "—"
    if distance_m >= 1000:
        return f"{distance_m / 1000:.1f} km"
    return f"{distance_m:.0f} m"


def _count_within(count: int | None, radius: int, tr: Tr) -> str:
    if count is None:
        return "—"
    return f"{count} (<{radius}m)"


def _value(value: int | None) -> str:
    return "—" if value is None else str(value)


def _short_timestamp(value: str) -> str:
    return value.split("T")[0] if value else "—"
