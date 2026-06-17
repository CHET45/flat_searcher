"""Core scoring block calculators."""

from __future__ import annotations

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel


def room_privacy_score(
    effective_private_rooms: int | None,
    target_private_rooms: int = 2,
) -> float:
    if effective_private_rooms is None:
        return 45.0
    if effective_private_rooms <= 0:
        return 0.0
    if effective_private_rooms == target_private_rooms:
        return 100.0
    if effective_private_rooms < target_private_rooms:
        return 45.0
    return 85.0


def layout_confidence_score(label: LayoutConfidenceLabel) -> float:
    return {
        LayoutConfidenceLabel.CONFIRMED: 100.0,
        LayoutConfidenceLabel.LIKELY: 80.0,
        LayoutConfidenceLabel.UNCLEAR: 45.0,
        LayoutConfidenceLabel.CONFLICT: 35.0,
    }[label]


def mortgage_suitability_score(level: MortgageRiskLevel) -> float:
    return {
        MortgageRiskLevel.LOW: 100.0,
        MortgageRiskLevel.MEDIUM: 70.0,
        MortgageRiskLevel.HIGH: 35.0,
        MortgageRiskLevel.CRITICAL: 5.0,
        MortgageRiskLevel.UNKNOWN: 50.0,
    }[level]
