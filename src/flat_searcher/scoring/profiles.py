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

    def weight_for(self, block_key: ScoreBlockKey) -> int:
        return self.block_importance.get(block_key, ImportanceLevel.IGNORE).weight


def default_living_mortgage_profile() -> ScoringProfile:
    return ScoringProfile(
        key="for_living_mortgage",
        name="For living + mortgage",
        block_importance={
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
            ScoreBlockKey.FLOOR: ImportanceLevel.IGNORE,
            ScoreBlockKey.CONDITION_RENOVATION: ImportanceLevel.IGNORE,
        },
    )
