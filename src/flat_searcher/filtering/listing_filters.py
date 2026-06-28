"""Filters for ranking and map candidate sets."""

from __future__ import annotations

from dataclasses import dataclass, field

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel


@dataclass(frozen=True)
class ListingCandidate:
    listing_id: int
    score: float | None
    listing_status: str = "active"
    user_status: str = "unseen"
    is_favorite: bool = False
    is_rejected: bool = False
    is_viewed: bool = False
    district: str | None = None
    street: str | None = None
    price_eur: int | None = None
    area_m2: float | None = None
    declared_rooms_ss: int | None = None
    effective_private_rooms: int | None = None
    room_conflict: bool = False
    kitchen_living_detected: bool = False
    layout_confidence_label: LayoutConfidenceLabel | None = None
    mortgage_risk_level: MortgageRiskLevel | None = None
    stove_heating_risk: bool = False
    wooden_building_risk: bool = False
    has_floor_plan: bool = False
    has_notes: bool = False
    price_value_score: float | None = None
    suspicious_low_price_flag: bool = False
    rtu_score: float | None = None
    transport_score: float | None = None
    station_score: float | None = None


@dataclass(frozen=True)
class ListingFilters:
    price_min: int | None = None
    price_max: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    districts: frozenset[str] = field(default_factory=frozenset)
    declared_rooms: frozenset[int] = field(default_factory=frozenset)
    effective_private_rooms: frozenset[int] = field(default_factory=frozenset)
    only_without_room_conflict: bool = False
    only_confirmed_layout: bool = False
    hide_high_mortgage_risk: bool = False
    hide_stove_heating: bool = False
    hide_wooden_buildings: bool = False
    only_with_floor_plan: bool = False
    only_good_transport: bool = False
    only_near_rtu: bool = False
    only_near_central_station: bool = False
    active_only: bool = True
    show_inactive: bool = False
    hide_viewed: bool = False
    hide_rejected: bool = True
    favorites_only: bool = False
    rejected_only: bool = False
    inactive_only: bool = False


def filter_candidates(
    candidates: tuple[ListingCandidate, ...],
    filters: ListingFilters,
) -> tuple[ListingCandidate, ...]:
    return tuple(candidate for candidate in candidates if _matches(candidate, filters))


def _matches(candidate: ListingCandidate, filters: ListingFilters) -> bool:
    if filters.active_only and not filters.show_inactive and candidate.listing_status != "active":
        return False
    if filters.inactive_only and candidate.listing_status == "active":
        return False
    if filters.hide_rejected and candidate.is_rejected:
        return False
    if filters.rejected_only and not candidate.is_rejected:
        return False
    if filters.favorites_only and not candidate.is_favorite:
        return False
    if filters.hide_viewed and candidate.is_viewed:
        return False
    if not _number_in_range(candidate.price_eur, filters.price_min, filters.price_max):
        return False
    if not _number_in_range(candidate.area_m2, filters.area_min, filters.area_max):
        return False
    if filters.districts and candidate.district not in filters.districts:
        return False
    if filters.declared_rooms and candidate.declared_rooms_ss not in filters.declared_rooms:
        return False
    if (
        filters.effective_private_rooms
        and candidate.effective_private_rooms not in filters.effective_private_rooms
    ):
        return False
    if filters.only_without_room_conflict and candidate.room_conflict:
        return False
    if (
        filters.only_confirmed_layout
        and candidate.layout_confidence_label != LayoutConfidenceLabel.CONFIRMED
    ):
        return False
    if filters.hide_high_mortgage_risk and candidate.mortgage_risk_level in {
        MortgageRiskLevel.HIGH,
        MortgageRiskLevel.CRITICAL,
    }:
        return False
    if filters.hide_stove_heating and candidate.stove_heating_risk:
        return False
    if filters.hide_wooden_buildings and candidate.wooden_building_risk:
        return False
    if filters.only_with_floor_plan and not candidate.has_floor_plan:
        return False
    if filters.only_good_transport and (candidate.transport_score or 0) < 70:
        return False
    if filters.only_near_rtu and (candidate.rtu_score or 0) < 70:
        return False
    if filters.only_near_central_station and (candidate.station_score or 0) < 70:
        return False
    return True


def _number_in_range(
    value: int | float | None,
    minimum: int | float | None,
    maximum: int | float | None,
) -> bool:
    if minimum is not None and (value is None or value < minimum):
        return False
    if maximum is not None and (value is None or value > maximum):
        return False
    return True
