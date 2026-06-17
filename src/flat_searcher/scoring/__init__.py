"""Score calculation package."""

from flat_searcher.scoring.blocks import (
    layout_confidence_score,
    mortgage_suitability_score,
    room_privacy_score,
)
from flat_searcher.scoring.engine import (
    BlockScore,
    ScorePenalty,
    ScoreResult,
    calculate_weighted_score,
)
from flat_searcher.scoring.price_value import (
    MarketBaseline,
    MarketBaselineLevel,
    MarketListing,
    PriceValueResult,
    calculate_price_value,
    choose_market_baseline,
    is_suspiciously_low_price,
    relative_market_score,
)
from flat_searcher.scoring.profiles import (
    ImportanceLevel,
    ScoreBlockKey,
    ScoringProfile,
    default_living_mortgage_profile,
)

__all__ = [
    "BlockScore",
    "ImportanceLevel",
    "ScoreBlockKey",
    "ScorePenalty",
    "ScoreResult",
    "ScoringProfile",
    "MarketBaseline",
    "MarketBaselineLevel",
    "MarketListing",
    "PriceValueResult",
    "calculate_price_value",
    "calculate_weighted_score",
    "choose_market_baseline",
    "default_living_mortgage_profile",
    "is_suspiciously_low_price",
    "layout_confidence_score",
    "mortgage_suitability_score",
    "relative_market_score",
    "room_privacy_score",
]
