"""Price-value market baseline calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from flat_searcher.ai import MortgageRiskLevel


class MarketBaselineLevel(StrEnum):
    RIGA = "riga"
    DISTRICT = "district"
    AI_ADJUSTED = "ai_adjusted"
    SERIES_BUILDING = "series_building"


@dataclass(frozen=True)
class MarketListing:
    listing_id: int
    price_eur: int | None
    area_m2: float | None
    district: str | None = None
    declared_rooms_ss: int | None = None
    effective_private_rooms: int | None = None
    building_series: str | None = None
    mortgage_risk_level: MortgageRiskLevel | None = None
    is_active: bool = True

    @property
    def price_per_m2(self) -> float | None:
        if self.price_eur is None or self.area_m2 is None or self.area_m2 <= 0:
            return None
        return self.price_eur / self.area_m2


@dataclass(frozen=True)
class MarketBaseline:
    level: MarketBaselineLevel
    sample_size: int
    median_price_per_m2: float
    explanation: str


@dataclass(frozen=True)
class PriceValueResult:
    price_per_m2_score: float | None
    relative_market_score: float | None
    suspicious_low_price_flag: bool
    baseline: MarketBaseline | None


def choose_market_baseline(
    target: MarketListing,
    listings: tuple[MarketListing, ...],
    minimum_sample_size: int = 5,
) -> MarketBaseline | None:
    candidates = _normal_comparables(target, listings)
    baseline_specs = (
        (MarketBaselineLevel.SERIES_BUILDING, _same_series),
        (MarketBaselineLevel.AI_ADJUSTED, _same_ai_room_context),
        (MarketBaselineLevel.DISTRICT, _same_district_context),
        (MarketBaselineLevel.RIGA, lambda item, target_listing: True),
    )

    fallback: MarketBaseline | None = None
    for level, predicate in baseline_specs:
        sample = tuple(item for item in candidates if predicate(item, target))
        baseline = _baseline_from_sample(level, sample)
        if baseline is None:
            continue
        if level == MarketBaselineLevel.RIGA:
            fallback = baseline
        if baseline.sample_size >= minimum_sample_size:
            return baseline
    return fallback


def calculate_price_value(
    target: MarketListing,
    listings: tuple[MarketListing, ...],
) -> PriceValueResult:
    baseline = choose_market_baseline(target, listings)
    price_per_m2 = target.price_per_m2
    if baseline is None or price_per_m2 is None:
        return PriceValueResult(None, None, False, baseline)

    relative_score = relative_market_score(price_per_m2, baseline.median_price_per_m2)
    return PriceValueResult(
        price_per_m2_score=relative_score,
        relative_market_score=relative_score,
        suspicious_low_price_flag=is_suspiciously_low_price(price_per_m2, baseline),
        baseline=baseline,
    )


def relative_market_score(price_per_m2: float, baseline_price_per_m2: float) -> float:
    if baseline_price_per_m2 <= 0:
        return 50.0
    ratio = price_per_m2 / baseline_price_per_m2
    if ratio <= 0.75:
        return 95.0
    if ratio <= 0.9:
        return 80.0
    if ratio <= 1.0:
        return 65.0
    if ratio <= 1.15:
        return 50.0
    if ratio <= 1.35:
        return 30.0
    return 10.0


def is_suspiciously_low_price(price_per_m2: float, baseline: MarketBaseline) -> bool:
    return baseline.sample_size >= 5 and price_per_m2 < baseline.median_price_per_m2 * 0.65


def _normal_comparables(
    target: MarketListing,
    listings: tuple[MarketListing, ...],
) -> tuple[MarketListing, ...]:
    normal = []
    for listing in listings:
        if listing.listing_id == target.listing_id:
            continue
        if not listing.is_active:
            continue
        if listing.mortgage_risk_level == MortgageRiskLevel.CRITICAL:
            continue
        price_per_m2 = listing.price_per_m2
        if price_per_m2 is None:
            continue
        if price_per_m2 < 100 or price_per_m2 > 10_000:
            continue
        normal.append(listing)
    return tuple(normal)


def _baseline_from_sample(
    level: MarketBaselineLevel,
    sample: tuple[MarketListing, ...],
) -> MarketBaseline | None:
    prices = tuple(item.price_per_m2 for item in sample if item.price_per_m2 is not None)
    if not prices:
        return None
    median_price = median(prices)
    return MarketBaseline(
        level=level,
        sample_size=len(prices),
        median_price_per_m2=round(float(median_price), 2),
        explanation=f"Baseline uses {len(prices)} active comparable listings at {level.value} level.",
    )


def _same_district_context(item: MarketListing, target: MarketListing) -> bool:
    if not target.district or item.district != target.district:
        return False
    if target.area_m2 and item.area_m2:
        area_ratio = item.area_m2 / target.area_m2
        if not 0.8 <= area_ratio <= 1.2:
            return False
    if target.declared_rooms_ss and item.declared_rooms_ss:
        if abs(item.declared_rooms_ss - target.declared_rooms_ss) > 1:
            return False
    return True


def _same_ai_room_context(item: MarketListing, target: MarketListing) -> bool:
    return (
        _same_district_context(item, target)
        and target.effective_private_rooms is not None
        and item.effective_private_rooms == target.effective_private_rooms
    )


def _same_series(item: MarketListing, target: MarketListing) -> bool:
    return (
        _same_district_context(item, target)
        and bool(target.building_series)
        and item.building_series == target.building_series
    )
