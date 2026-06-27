"""Score recalculation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from flat_searcher.ai import MortgageRiskLevel
from flat_searcher.db.repository import open_database
from flat_searcher.db.scoring_repository import ListingForScoring, ScoringRepository
from flat_searcher.scoring import (
    BlockScore,
    MarketListing,
    ScoreBlockKey,
    calculate_price_value,
    calculate_weighted_score,
    default_living_mortgage_profile,
    layout_confidence_score,
    mortgage_suitability_score,
    room_privacy_score,
)


@dataclass(frozen=True)
class ScoreRecalculationResult:
    listing_count: int
    scored_count: int


class ScoreRecalculationService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def recalculate(self, profile_key: str = "for_living_mortgage") -> ScoreRecalculationResult:
        if profile_key != "for_living_mortgage":
            raise ValueError(f"Unsupported profile: {profile_key}")
        profile = default_living_mortgage_profile()

        with open_database(self.database_path) as connection:
            repository = ScoringRepository(connection)
            listings = repository.load_active_listings()
            repository.save_profile(profile)
            market_listings = tuple(_market_listing(listing) for listing in listings)
            median_area = _median_area(listings)
            scored_count = 0

            for listing in listings:
                price_value = calculate_price_value(
                    _market_listing(listing),
                    market_listings,
                )
                blocks = _block_scores(listing, price_value.relative_market_score, median_area)
                result = calculate_weighted_score(profile, blocks)
                calculated_at = _now()
                repository.save_price_value(listing, price_value, calculated_at)
                repository.save_score_result(listing.listing_id, result, calculated_at)
                if result.overall_score is not None:
                    scored_count += 1

            return ScoreRecalculationResult(
                listing_count=len(listings),
                scored_count=scored_count,
            )


def _block_scores(
    listing: ListingForScoring,
    price_value_score: float | None,
    median_area: float | None,
) -> tuple[BlockScore, ...]:
    return (
        BlockScore(
            ScoreBlockKey.PRICE_VALUE,
            price_value_score,
            "Relative price per square meter compared with active listings.",
        ),
        BlockScore(
            ScoreBlockKey.ROOM_PRIVACY,
            room_privacy_score(listing.effective_private_rooms),
            "Two effective private rooms are the default target.",
        ),
        BlockScore(
            ScoreBlockKey.LAYOUT_CONFIDENCE,
            None
            if listing.layout_confidence_label is None
            else layout_confidence_score(listing.layout_confidence_label),
            "Confidence in the current layout conclusion.",
        ),
        BlockScore(
            ScoreBlockKey.MORTGAGE_SUITABILITY,
            mortgage_suitability_score(
                listing.mortgage_risk_level or MortgageRiskLevel.UNKNOWN
            ),
            "Mortgage suitability derived from the latest analysis.",
        ),
        BlockScore(
            ScoreBlockKey.RTU_ACCESSIBILITY,
            listing.rtu_score,
            "Distance-based RTU accessibility.",
        ),
        BlockScore(
            ScoreBlockKey.TRANSPORT_CONNECTIVITY,
            listing.transport_score,
            "Nearby public transport stops.",
        ),
        BlockScore(
            ScoreBlockKey.CENTRAL_STATION_ACCESSIBILITY,
            listing.station_score,
            "Distance-based central station accessibility.",
        ),
        BlockScore(
            ScoreBlockKey.SHOPS_INFRASTRUCTURE,
            listing.shop_score,
            "Nearby grocery infrastructure.",
        ),
        BlockScore(
            ScoreBlockKey.USEFUL_AREA,
            _useful_area_score(listing.area_m2, median_area),
            "Area compared with the active listing set.",
        ),
        BlockScore(
            ScoreBlockKey.BUILDING_SERIES,
            55.0 if listing.building_series else None,
            "Neutral initial score when building series data is available.",
        ),
    )


def _market_listing(listing: ListingForScoring) -> MarketListing:
    return MarketListing(
        listing_id=listing.listing_id,
        price_eur=listing.price_eur,
        area_m2=listing.area_m2,
        district=listing.district,
        declared_rooms_ss=listing.declared_rooms_ss,
        effective_private_rooms=listing.effective_private_rooms,
        building_series=listing.building_series,
        mortgage_risk_level=listing.mortgage_risk_level,
        is_active=listing.listing_status == "active",
    )


def _median_area(listings: tuple[ListingForScoring, ...]) -> float | None:
    areas = [listing.area_m2 for listing in listings if listing.area_m2 and listing.area_m2 > 0]
    return None if not areas else float(median(areas))


def _useful_area_score(area_m2: float | None, median_area: float | None) -> float | None:
    if area_m2 is None or area_m2 <= 0 or median_area is None or median_area <= 0:
        return None
    ratio = area_m2 / median_area
    if ratio >= 1.3:
        return 95.0
    if ratio >= 1.05:
        return 80.0
    if ratio >= 0.8:
        return 65.0
    if ratio >= 0.6:
        return 45.0
    return 25.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
