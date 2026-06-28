"""UI-facing view models with English display text."""

from __future__ import annotations

import json
from dataclasses import dataclass

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.db.read_models import ListingDetailReadModel
from flat_searcher.filtering import ListingCandidate
from flat_searcher.presentation.titles import format_ai_room_label, format_apartment_title
from flat_searcher.ranking import RankedCandidate
from flat_searcher.scoring import BLOCK_LABELS, ScoreBlockKey


@dataclass(frozen=True)
class RankingRowViewModel:
    position: int
    listing_id: int
    title: str
    score_text: str
    price_text: str
    price_per_m2_text: str
    area_text: str
    layout_text: str
    mortgage_text: str
    status_text: str
    flags_text: str


@dataclass(frozen=True)
class DetailViewModel:
    listing_id: int
    title: str
    top_lines: tuple[str, ...]
    flags_lines: tuple[str, ...]
    rating_lines: tuple[str, ...]
    price_value_lines: tuple[str, ...]
    layout_lines: tuple[str, ...]
    mortgage_lines: tuple[str, ...]
    location_lines: tuple[str, ...]
    history_lines: tuple[str, ...]
    original_listing_text: str
    ss_url: str
    floor_plan_path: str | None = None


def ranking_row_view_model(ranked: RankedCandidate) -> RankingRowViewModel:
    candidate = ranked.candidate
    return RankingRowViewModel(
        position=ranked.position,
        listing_id=candidate.listing_id,
        title=_title_from_candidate(candidate),
        score_text=_score_text(candidate.score),
        price_text=_price_text(candidate.price_eur),
        price_per_m2_text=_price_per_m2_text(
            _derive_price_per_m2(candidate.price_eur, candidate.area_m2)
        ),
        area_text=_area_text(candidate.area_m2),
        layout_text=_layout_text(candidate),
        mortgage_text=(
            "Unknown" if candidate.mortgage_risk_level is None else candidate.mortgage_risk_level.value
        ),
        status_text=candidate.user_status,
        flags_text=", ".join(key_flags(candidate)),
    )


def key_flags(candidate: ListingCandidate) -> tuple[str, ...]:
    """Short, user-facing flags summarising the most important candidate signals."""

    flags: list[str] = []
    if candidate.is_favorite:
        flags.append("Favorite")
    if candidate.has_notes:
        flags.append("Note")
    if candidate.room_conflict:
        flags.append("Room conflict")
    if candidate.layout_confidence_label == LayoutConfidenceLabel.CONFIRMED:
        flags.append("Layout confirmed")
    if candidate.has_floor_plan:
        flags.append("Floor plan")
    if candidate.kitchen_living_detected:
        flags.append("Kitchen-living")
    if candidate.price_value_score is not None and candidate.price_value_score >= 80:
        flags.append("Good price")
    if candidate.suspicious_low_price_flag:
        flags.append("Suspiciously low price - check carefully")
    if candidate.mortgage_risk_level in {
        MortgageRiskLevel.HIGH,
        MortgageRiskLevel.CRITICAL,
    }:
        flags.append("High mortgage risk")
    if candidate.stove_heating_risk:
        flags.append("Stove heating")
    if candidate.wooden_building_risk:
        flags.append("Wooden building")
    return tuple(flags)


def detail_view_model(detail: ListingDetailReadModel) -> DetailViewModel:
    title = format_apartment_title(
        district=detail.district,
        street=detail.street,
        effective_private_rooms=detail.effective_private_rooms,
        declared_rooms_ss=detail.declared_rooms_ss,
        area_m2=detail.area_m2,
        price_eur=detail.price_eur,
        kitchen_living_detected=detail.kitchen_living_detected,
    )
    return DetailViewModel(
        listing_id=detail.listing_id,
        title=title,
        top_lines=_top_lines(detail),
        flags_lines=_flags_lines(detail),
        rating_lines=_rating_lines(detail),
        price_value_lines=_price_value_lines(detail),
        layout_lines=_layout_lines(detail),
        mortgage_lines=_mortgage_lines(detail),
        location_lines=_location_lines(detail),
        history_lines=_history_lines(detail),
        original_listing_text=detail.description_text or "",
        ss_url=detail.ss_url,
        floor_plan_path=detail.floor_plan_path,
    )


def _title_from_candidate(candidate: ListingCandidate) -> str:
    return format_apartment_title(
        district=candidate.district,
        street=candidate.street,
        effective_private_rooms=candidate.effective_private_rooms,
        declared_rooms_ss=candidate.declared_rooms_ss,
        area_m2=candidate.area_m2,
        price_eur=candidate.price_eur,
        kitchen_living_detected=candidate.kitchen_living_detected,
    )


def _top_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    return (
        f"Price: {_price_text(detail.price_eur)}",
        f"Area: {_area_text(detail.area_m2)}",
        f"Price per m2: {_price_per_m2_text(detail.price_per_m2)}",
        f"District: {detail.district or 'unknown'}",
        f"Street: {detail.street or 'unknown'}",
        f"Floor: {_floor_text(detail.floor, detail.total_floors)}",
        f"Building series: {detail.building_series or 'unknown'}",
        f"Building type: {detail.building_type or 'unknown'}",
        f"Listing date: {detail.listing_date_text or 'unknown'}",
    )


def _flags_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    flags: list[str] = []
    if detail.is_favorite:
        flags.append("Favorite")
    if detail.is_rejected:
        flags.append("Rejected")
    if detail.listing_status == "inactive":
        flags.append("Inactive")
    if detail.ss_vs_ai_room_conflict:
        flags.append("Room conflict")
    if detail.layout_confidence_label == LayoutConfidenceLabel.UNCLEAR:
        flags.append("Layout unclear")
    if detail.has_floor_plan and detail.layout_confidence_label == LayoutConfidenceLabel.CONFIRMED:
        flags.append("Layout confirmed by floor plan")
    elif detail.has_floor_plan:
        flags.append("Floor plan")
    if detail.kitchen_living_detected:
        flags.append("Kitchen-living is not counted as private room")
    if detail.mortgage_risk_level in {MortgageRiskLevel.HIGH, MortgageRiskLevel.CRITICAL}:
        flags.append("High mortgage risk")
    if detail.stove_heating_risk:
        flags.append("Stove heating risk")
    if detail.wooden_building_risk:
        flags.append("Wooden building risk")
    if detail.suspicious_low_price_flag:
        flags.append("Suspiciously low price - check carefully")
    if detail.geo_scores_disabled_reason:
        flags.append(detail.geo_scores_disabled_reason)
    if detail.declared_rooms_ss is not None and detail.effective_private_rooms is not None:
        flags.append(
            "AI: "
            f"{format_ai_room_label(detail.effective_private_rooms, detail.kitchen_living_detected)} "
            f"/ SS: {detail.declared_rooms_ss}"
        )
    return tuple(flags) or ("No major flags",)


def _rating_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    lines = [
        f"Overall score: {_score_text(detail.overall_score)}",
    ]
    if detail.score_explanation:
        lines.append(f"Explanation: {detail.score_explanation}")
    breakdown = _score_breakdown_lines(detail.score_breakdown_json)
    if breakdown:
        lines.append("Breakdown:")
        lines.extend(breakdown)
    return tuple(lines)


def _price_value_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    return (
        f"Price value score: {_score_text(detail.price_value_score)}",
        f"Price per m2 score: {_score_text(detail.price_per_m2_score)}",
        f"Relative market score: {_score_text(detail.relative_market_score)}",
        "Price per effective private room: "
        f"{_money_text(detail.price_per_effective_private_room)}",
        "Private room value score: "
        f"{_score_text(detail.price_per_effective_private_room_score)}",
        f"Absolute price score: {_score_text(detail.absolute_price_score)}",
        "Suspiciously low price: "
        f"{_yes_no(detail.suspicious_low_price_flag)}",
        f"Baseline level: {detail.market_baseline_level_used or 'unknown'}",
        f"Baseline sample size: {_optional_count(detail.market_baseline_sample_size)}",
        "Baseline median EUR/m2: "
        f"{_price_per_m2_text(detail.market_baseline_median_price_per_m2)}",
        f"Baseline explanation: {detail.market_baseline_explanation or 'not calculated yet'}",
    )


def _layout_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    return (
        f"AI-effective private rooms: {_value_text(detail.effective_private_rooms)}",
        f"SS-declared rooms: {_value_text(detail.declared_rooms_ss)}",
        f"Walkthrough rooms: {_value_text(detail.walkthrough_rooms)}",
        f"Kitchen-living detected: {_yes_no(detail.kitchen_living_detected)}",
        "Layout confidence: "
        + ("Unknown" if detail.layout_confidence_label is None else detail.layout_confidence_label.value),
        f"Explanation: {detail.layout_explanation_user or 'not analyzed yet'}",
    )


def _mortgage_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    return (
        "Mortgage risk: "
        + ("Unknown" if detail.mortgage_risk_level is None else detail.mortgage_risk_level.value),
        f"Reasons: {detail.mortgage_risk_reasons or 'not analyzed yet'}",
        f"Evidence: {detail.mortgage_explanation_user or 'not analyzed yet'}",
    )


def _location_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    precision = "unknown" if detail.geocode_precision is None else detail.geocode_precision.value
    confidence = "unknown" if detail.geocode_confidence is None else detail.geocode_confidence.value
    return (
        f"Address precision: {precision}",
        f"Geocode confidence: {confidence}",
        f"Coordinates: {_coordinates_text(detail.latitude, detail.longitude)}",
        f"Location scores enabled: {_yes_no(detail.geo_scores_enabled)}",
        f"Disabled reason: {detail.geo_scores_disabled_reason or 'none'}",
        "RTU: "
        f"{_distance_text(detail.distance_to_rtu_m)}, "
        f"score {_score_text(detail.rtu_score)}",
        "Central station: "
        f"{_distance_text(detail.distance_to_central_station_m)}, "
        f"score {_score_text(detail.station_score)}",
        "Grocery shops: "
        f"nearest {_distance_text(detail.nearest_shop_distance_m)}, "
        f"{_optional_count(detail.shops_within_1200m)} within 1200 m, "
        f"score {_score_text(detail.shop_score)}",
        "Public transport: "
        f"nearest {_distance_text(detail.nearest_transport_stop_distance_m)}, "
        f"{_optional_count(detail.transport_stops_nearby_count)} within 900 m, "
        f"score {_score_text(detail.transport_score)}",
        f"Location explanation: {detail.location_explanation or 'not calculated yet'}",
    )


def _history_lines(detail: ListingDetailReadModel) -> tuple[str, ...]:
    lines = [
        f"Snapshots: {len(detail.history_snapshots)}",
        f"Change events: {len(detail.change_events)}",
    ]
    if detail.history_snapshots:
        latest = detail.history_snapshots[0]
        lines.append(f"Last checked: {latest.checked_at}")
        lines.append(f"Latest price: {_price_text(latest.price_eur)}")
        lines.append(f"Latest unique visits: {_optional_count(latest.unique_visits)}")
        lines.append(f"Latest image count: {_optional_count(latest.images_count)}")
        lines.append(f"Latest active status: {_yes_no(latest.is_active)}")
    if detail.change_events:
        latest_event = detail.change_events[0]
        event_value = latest_event.event_type
        if latest_event.old_value is not None or latest_event.new_value is not None:
            event_value += f" ({latest_event.old_value} -> {latest_event.new_value})"
        lines.append(f"Latest event: {event_value}")
    return tuple(lines)


def _score_breakdown_lines(score_breakdown_json: str | None) -> tuple[str, ...]:
    if not score_breakdown_json:
        return ()
    try:
        raw = json.loads(score_breakdown_json)
    except json.JSONDecodeError:
        return ()
    if not isinstance(raw, dict):
        return ()
    lines: list[str] = []
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        score = value.get("score")
        explanation = value.get("explanation")
        label = _block_label(str(key))
        score_text = _score_text(float(score)) if isinstance(score, int | float) else "unknown"
        if isinstance(explanation, str) and explanation:
            lines.append(f"{label}: {score_text} - {explanation}")
        else:
            lines.append(f"{label}: {score_text}")
    return tuple(lines)


def _block_label(raw_key: str) -> str:
    try:
        return BLOCK_LABELS[ScoreBlockKey(raw_key)]
    except (KeyError, ValueError):
        return raw_key.replace("_", " ").title()


def _layout_text(candidate: ListingCandidate) -> str:
    confidence = (
        "Unknown" if candidate.layout_confidence_label is None else candidate.layout_confidence_label.value
    )
    return f"{_value_text(candidate.effective_private_rooms)} private, {confidence}"


def _score_text(score: float | None) -> str:
    return "unknown" if score is None else f"{score:.1f}"


def _price_text(price_eur: int | None) -> str:
    if price_eur is None:
        return "unknown"
    return f"{price_eur:,}".replace(",", " ") + " EUR"


def _money_text(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:,.0f}".replace(",", " ") + " EUR"


def _derive_price_per_m2(price_eur: int | None, area_m2: float | None) -> float | None:
    if price_eur is None or area_m2 is None or area_m2 <= 0:
        return None
    return price_eur / area_m2


def _price_per_m2_text(price_per_m2: float | None) -> str:
    if price_per_m2 is None:
        return "unknown"
    return f"{price_per_m2:,.2f}".replace(",", " ") + " EUR/m2"


def _area_text(area_m2: float | None) -> str:
    if area_m2 is None:
        return "unknown"
    value = float(area_m2)
    return f"{int(value)} m2" if value.is_integer() else f"{value:.1f} m2"


def _floor_text(floor: int | None, total_floors: int | None) -> str:
    if floor is None:
        return "unknown"
    if total_floors is None:
        return str(floor)
    return f"{floor}/{total_floors}"


def _coordinates_text(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return "unknown"
    return f"{latitude:.6f}, {longitude:.6f}"


def _distance_text(distance_m: float | None) -> str:
    return "unknown" if distance_m is None else f"{distance_m:.0f} m"


def _optional_count(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _value_text(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"
