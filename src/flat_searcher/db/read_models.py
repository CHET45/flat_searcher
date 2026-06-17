"""Read models used by ranking, detail and map surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.geo import AddressPrecision, GeocodeConfidence


@dataclass(frozen=True)
class ListingHistorySnapshot:
    checked_at: str
    price_eur: int | None
    unique_visits: int | None
    description_hash: str | None
    images_count: int | None
    is_active: bool
    raw_snapshot_hash: str | None


@dataclass(frozen=True)
class ListingChangeEvent:
    detected_at: str
    event_type: str
    old_value: str | None
    new_value: str | None
    delta_value: str | None
    explanation: str | None


@dataclass(frozen=True)
class ListingDetailReadModel:
    listing_id: int
    ss_id: str
    ss_url: str
    listing_status: str
    user_status: str
    is_favorite: bool
    is_rejected: bool
    is_viewed: bool
    user_notes: str | None

    district: str | None
    street: str | None
    house_number: str | None
    address_raw: str | None
    price_eur: int | None
    price_per_m2: float | None
    area_m2: float | None
    declared_rooms_ss: int | None
    floor: int | None
    total_floors: int | None
    building_series: str | None
    building_type: str | None
    listing_date_text: str | None
    unique_visits: int | None
    description_text: str | None

    effective_private_rooms: int | None
    walkthrough_rooms: int | None
    kitchen_living_detected: bool
    layout_confidence_label: LayoutConfidenceLabel | None
    layout_explanation_user: str | None
    mortgage_risk_level: MortgageRiskLevel | None
    mortgage_risk_reasons: str | None
    mortgage_explanation_user: str | None

    latitude: float | None
    longitude: float | None
    geocode_precision: AddressPrecision | None
    geocode_confidence: GeocodeConfidence | None
    geo_scores_enabled: bool
    geo_scores_disabled_reason: str | None

    overall_score: float | None
    history_snapshots: tuple[ListingHistorySnapshot, ...]
    change_events: tuple[ListingChangeEvent, ...]


def parse_layout_confidence(value: str | None) -> LayoutConfidenceLabel | None:
    if value is None:
        return None
    try:
        return LayoutConfidenceLabel(value)
    except ValueError:
        return None


def parse_mortgage_risk(value: str | None) -> MortgageRiskLevel | None:
    if value is None:
        return None
    try:
        return MortgageRiskLevel(value)
    except ValueError:
        return None


def parse_address_precision(value: str | None) -> AddressPrecision | None:
    if value is None:
        return None
    try:
        return AddressPrecision(value)
    except ValueError:
        return None


def parse_geocode_confidence(value: str | None) -> GeocodeConfidence | None:
    if value is None:
        return None
    try:
        return GeocodeConfidence(value)
    except ValueError:
        return None
