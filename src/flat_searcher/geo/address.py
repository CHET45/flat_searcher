"""Address precision rules for location-sensitive scoring."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AddressPrecision(StrEnum):
    EXACT_HOUSE = "exact_house"
    STREET_APPROX = "street_approx"
    DISTRICT_APPROX = "district_approx"
    UNKNOWN = "unknown"


class GeocodeConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class AddressScoreEligibility:
    precision: AddressPrecision
    confidence: GeocodeConfidence | None
    geo_scores_enabled: bool
    disabled_reason: str | None


def determine_address_precision(
    district: str | None,
    street: str | None,
    house_number: str | None,
) -> AddressPrecision:
    if street and house_number:
        return AddressPrecision.EXACT_HOUSE
    if street:
        return AddressPrecision.STREET_APPROX
    if district:
        return AddressPrecision.DISTRICT_APPROX
    return AddressPrecision.UNKNOWN


def location_score_eligibility(
    precision: AddressPrecision,
    confidence: GeocodeConfidence | None,
) -> AddressScoreEligibility:
    if precision == AddressPrecision.EXACT_HOUSE and confidence == GeocodeConfidence.HIGH:
        return AddressScoreEligibility(
            precision=precision,
            confidence=confidence,
            geo_scores_enabled=True,
            disabled_reason=None,
        )
    return AddressScoreEligibility(
        precision=precision,
        confidence=confidence,
        geo_scores_enabled=False,
        disabled_reason=_disabled_reason(precision, confidence),
    )


def _disabled_reason(
    precision: AddressPrecision,
    confidence: GeocodeConfidence | None,
) -> str:
    if precision == AddressPrecision.STREET_APPROX:
        return "Approximate address - location scores not calculated"
    if precision == AddressPrecision.DISTRICT_APPROX:
        return "District-level address - location scores not calculated"
    if precision == AddressPrecision.UNKNOWN:
        return "Address unknown - location scores not calculated"
    if confidence != GeocodeConfidence.HIGH:
        return "Geocode confidence is not high - location scores not calculated"
    return "Location scores not calculated"
