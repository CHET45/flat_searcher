"""Scoring profile definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScoreBlockKey(StrEnum):
    PRICE_VALUE = "price_value"
    USEFUL_AREA = "useful_area"
    ROOM_PRIVACY = "room_privacy"
    LAYOUT_CONFIDENCE = "layout_confidence"
    MORTGAGE_SUITABILITY = "mortgage_suitability"
    RTU_ACCESSIBILITY = "rtu_accessibility"
    TRANSPORT_CONNECTIVITY = "transport_connectivity"
    CENTRAL_STATION_ACCESSIBILITY = "central_station_accessibility"
    SHOPS_INFRASTRUCTURE = "shops_infrastructure"
    BUILDING_SERIES = "building_series"
    FLOOR = "floor"
    CONDITION_RENOVATION = "condition_renovation"


class ImportanceLevel(StrEnum):
    IGNORE = "Ignore"
    WEAK = "Weak factor"
    MEDIUM = "Medium factor"
    STRONG = "Strong factor"
    CRITICAL = "Critical factor"

    @property
    def weight(self) -> int:
        return {
            ImportanceLevel.IGNORE: 0,
            ImportanceLevel.WEAK: 1,
            ImportanceLevel.MEDIUM: 2,
            ImportanceLevel.STRONG: 3,
            ImportanceLevel.CRITICAL: 5,
        }[self]


@dataclass(frozen=True)
class ScoringProfile:
    key: str
    name: str
    block_importance: dict[ScoreBlockKey, ImportanceLevel]
    base_profile_key: str | None = None
    is_builtin: bool = True

    def weight_for(self, block_key: ScoreBlockKey) -> int:
        return self.block_importance.get(block_key, ImportanceLevel.IGNORE).weight


def _profile(
    key: str,
    name: str,
    importance: dict[ScoreBlockKey, ImportanceLevel],
    *,
    base_profile_key: str | None = None,
    is_builtin: bool = True,
) -> ScoringProfile:
    """Build a profile, defaulting every unspecified block to ``Ignore``."""

    block_importance = {block: ImportanceLevel.IGNORE for block in ScoreBlockKey}
    block_importance.update(importance)
    return ScoringProfile(
        key=key,
        name=name,
        block_importance=block_importance,
        base_profile_key=base_profile_key,
        is_builtin=is_builtin,
    )


def default_living_mortgage_profile() -> ScoringProfile:
    return _profile(
        "for_living_mortgage",
        "For living + mortgage",
        {
            ScoreBlockKey.PRICE_VALUE: ImportanceLevel.CRITICAL,
            ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.STRONG,
            ScoreBlockKey.MORTGAGE_SUITABILITY: ImportanceLevel.STRONG,
            ScoreBlockKey.RTU_ACCESSIBILITY: ImportanceLevel.MEDIUM,
            ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.MEDIUM,
            ScoreBlockKey.CENTRAL_STATION_ACCESSIBILITY: ImportanceLevel.WEAK,
            ScoreBlockKey.SHOPS_INFRASTRUCTURE: ImportanceLevel.WEAK,
            ScoreBlockKey.USEFUL_AREA: ImportanceLevel.WEAK,
            ScoreBlockKey.BUILDING_SERIES: ImportanceLevel.WEAK,
            ScoreBlockKey.LAYOUT_CONFIDENCE: ImportanceLevel.WEAK,
        },
    )


def _builtin_profiles() -> tuple[ScoringProfile, ...]:
    return (
        default_living_mortgage_profile(),
        _profile(
            "mortgage_first",
            "Mortgage first",
            {
                ScoreBlockKey.MORTGAGE_SUITABILITY: ImportanceLevel.CRITICAL,
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.STRONG,
                ScoreBlockKey.BUILDING_SERIES: ImportanceLevel.MEDIUM,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.LAYOUT_CONFIDENCE: ImportanceLevel.WEAK,
                ScoreBlockKey.USEFUL_AREA: ImportanceLevel.WEAK,
            },
        ),
        _profile(
            "maximum_opportunity",
            "Maximum opportunity",
            {
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.CRITICAL,
                ScoreBlockKey.USEFUL_AREA: ImportanceLevel.MEDIUM,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.RTU_ACCESSIBILITY: ImportanceLevel.WEAK,
                ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.WEAK,
                ScoreBlockKey.MORTGAGE_SUITABILITY: ImportanceLevel.WEAK,
            },
        ),
        _profile(
            "only_two_private_rooms",
            "Only 2 private rooms",
            {
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.CRITICAL,
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.STRONG,
                ScoreBlockKey.LAYOUT_CONFIDENCE: ImportanceLevel.MEDIUM,
                ScoreBlockKey.MORTGAGE_SUITABILITY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.USEFUL_AREA: ImportanceLevel.WEAK,
            },
        ),
        _profile(
            "best_price",
            "Best price",
            {
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.CRITICAL,
                ScoreBlockKey.USEFUL_AREA: ImportanceLevel.WEAK,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.WEAK,
            },
        ),
        _profile(
            "best_transport",
            "Best transport",
            {
                ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.CRITICAL,
                ScoreBlockKey.CENTRAL_STATION_ACCESSIBILITY: ImportanceLevel.STRONG,
                ScoreBlockKey.RTU_ACCESSIBILITY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.SHOPS_INFRASTRUCTURE: ImportanceLevel.MEDIUM,
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.MEDIUM,
            },
        ),
        _profile(
            "closer_to_rtu",
            "Closer to RTU",
            {
                ScoreBlockKey.RTU_ACCESSIBILITY: ImportanceLevel.CRITICAL,
                ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.MEDIUM,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.MEDIUM,
            },
        ),
        _profile(
            "cash_purchase",
            "Cash purchase",
            {
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.CRITICAL,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.STRONG,
                ScoreBlockKey.USEFUL_AREA: ImportanceLevel.MEDIUM,
                ScoreBlockKey.RTU_ACCESSIBILITY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.LAYOUT_CONFIDENCE: ImportanceLevel.WEAK,
            },
        ),
        _profile(
            "investment_option",
            "Investment option",
            {
                ScoreBlockKey.PRICE_VALUE: ImportanceLevel.CRITICAL,
                ScoreBlockKey.TRANSPORT_CONNECTIVITY: ImportanceLevel.STRONG,
                ScoreBlockKey.CENTRAL_STATION_ACCESSIBILITY: ImportanceLevel.STRONG,
                ScoreBlockKey.SHOPS_INFRASTRUCTURE: ImportanceLevel.MEDIUM,
                ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.MEDIUM,
                ScoreBlockKey.MORTGAGE_SUITABILITY: ImportanceLevel.WEAK,
            },
        ),
    )


def builtin_profiles() -> dict[str, ScoringProfile]:
    """Return all built-in profiles keyed by profile key, ordered for display."""

    return {profile.key: profile for profile in _builtin_profiles()}


def custom_profile(
    key: str,
    name: str,
    importance: dict[ScoreBlockKey, ImportanceLevel],
    base_profile_key: str | None = None,
) -> ScoringProfile:
    """Build a user-defined profile. Importance is the only thing users control."""

    return _profile(
        key,
        name,
        importance,
        base_profile_key=base_profile_key,
        is_builtin=False,
    )


def slugify_profile_name(name: str) -> str:
    """Derive a stable profile key from a user-entered name."""

    cleaned = [character.lower() if character.isalnum() else "_" for character in name.strip()]
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"custom_{slug}" if slug else "custom_profile"


def builtin_profile(profile_key: str) -> ScoringProfile | None:
    return builtin_profiles().get(profile_key)


BLOCK_LABELS: dict[ScoreBlockKey, str] = {
    ScoreBlockKey.PRICE_VALUE: "Price value",
    ScoreBlockKey.ROOM_PRIVACY: "Room privacy / effective private rooms",
    ScoreBlockKey.MORTGAGE_SUITABILITY: "Mortgage suitability",
    ScoreBlockKey.RTU_ACCESSIBILITY: "RTU accessibility",
    ScoreBlockKey.TRANSPORT_CONNECTIVITY: "Transport connectivity",
    ScoreBlockKey.CENTRAL_STATION_ACCESSIBILITY: "Central station accessibility",
    ScoreBlockKey.SHOPS_INFRASTRUCTURE: "Shops / infrastructure",
    ScoreBlockKey.USEFUL_AREA: "Useful area",
    ScoreBlockKey.BUILDING_SERIES: "Building / series",
    ScoreBlockKey.LAYOUT_CONFIDENCE: "AI layout confidence",
    ScoreBlockKey.FLOOR: "Floor",
    ScoreBlockKey.CONDITION_RENOVATION: "Condition / renovation",
}
