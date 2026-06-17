"""Deterministic product rules applied after AI analysis."""

from __future__ import annotations

from dataclasses import dataclass

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel, Pass2ListingAnalysis


@dataclass(frozen=True)
class ListingProductAnalysis:
    layout_flags: tuple[str, ...]
    mortgage_flags: tuple[str, ...]
    all_flags: tuple[str, ...]
    effective_private_rooms: int | None
    declared_rooms_ss: int | None
    room_conflict: bool
    mortgage_risk_level: MortgageRiskLevel


def build_listing_product_analysis(
    analysis: Pass2ListingAnalysis,
    declared_rooms_ss: int | None,
) -> ListingProductAnalysis:
    layout_flags = _layout_flags(analysis, declared_rooms_ss)
    mortgage_flags = _mortgage_flags(analysis)
    return ListingProductAnalysis(
        layout_flags=layout_flags,
        mortgage_flags=mortgage_flags,
        all_flags=layout_flags + mortgage_flags,
        effective_private_rooms=analysis.effective_private_rooms,
        declared_rooms_ss=declared_rooms_ss,
        room_conflict=_has_room_conflict(analysis, declared_rooms_ss),
        mortgage_risk_level=analysis.mortgage_risk_level,
    )


def _layout_flags(
    analysis: Pass2ListingAnalysis,
    declared_rooms_ss: int | None,
) -> tuple[str, ...]:
    flags = []
    if _has_room_conflict(analysis, declared_rooms_ss):
        flags.append("Room conflict")
        if analysis.effective_private_rooms is not None and declared_rooms_ss is not None:
            flags.append(f"AI: {analysis.effective_private_rooms} private / SS: {declared_rooms_ss}")
    if analysis.layout_confidence_label == LayoutConfidenceLabel.UNCLEAR:
        flags.append("Layout unclear")
    if (
        analysis.floor_plan_image_ids
        and analysis.layout_confidence_label == LayoutConfidenceLabel.CONFIRMED
    ):
        flags.append("Layout confirmed by floor plan")
    if analysis.kitchen_living_detected:
        flags.append("Kitchen-living is not counted as private room")
    return tuple(flags)


def _mortgage_flags(analysis: Pass2ListingAnalysis) -> tuple[str, ...]:
    flags = []
    if analysis.mortgage_risk_level in {MortgageRiskLevel.HIGH, MortgageRiskLevel.CRITICAL}:
        flags.append("High mortgage risk")
    if analysis.stove_heating_risk:
        flags.append("Stove heating risk")
    if analysis.wooden_building_risk:
        flags.append("Wooden building risk")
    return tuple(flags)


def _has_room_conflict(
    analysis: Pass2ListingAnalysis,
    declared_rooms_ss: int | None,
) -> bool:
    if analysis.ss_vs_ai_room_conflict:
        return True
    if analysis.effective_private_rooms is None or declared_rooms_ss is None:
        return False
    return analysis.effective_private_rooms != declared_rooms_ss
