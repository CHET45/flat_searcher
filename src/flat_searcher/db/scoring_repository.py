"""Persistence and input records for score recalculation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.scoring import PriceValueResult, ScoreResult, ScoringProfile


@dataclass(frozen=True)
class ListingForScoring:
    listing_id: int
    price_eur: int | None
    area_m2: float | None
    district: str | None
    declared_rooms_ss: int | None
    building_series: str | None
    effective_private_rooms: int | None
    layout_confidence_label: LayoutConfidenceLabel | None
    mortgage_risk_level: MortgageRiskLevel | None
    rtu_score: float | None
    transport_score: float | None
    station_score: float | None
    shop_score: float | None
    listing_status: str


class ScoringRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_active_listings(self) -> tuple[ListingForScoring, ...]:
        rows = self.connection.execute(
            """
            SELECT
                l.id AS listing_id,
                l.price_eur,
                l.area_m2,
                l.district,
                l.declared_rooms_ss,
                l.building_series,
                l.listing_status,
                a.effective_private_rooms,
                a.layout_confidence_label,
                a.mortgage_risk_level,
                ls.rtu_score,
                ls.transport_score,
                ls.station_score,
                ls.shop_score
            FROM listings l
            LEFT JOIN latest_ai_analyses a ON a.listing_id = l.id
            LEFT JOIN location_scores ls ON ls.listing_id = l.id
            WHERE l.listing_status = 'active'
            ORDER BY l.id
            """
        ).fetchall()
        return tuple(_listing_for_scoring(row) for row in rows)

    def save_profile(self, profile: ScoringProfile) -> None:
        weights = {
            block_key.value: importance.value
            for block_key, importance in profile.block_importance.items()
        }
        enabled_blocks = [
            block_key.value
            for block_key, importance in profile.block_importance.items()
            if importance.weight > 0
        ]
        self.connection.execute(
            """
            INSERT INTO scoring_profiles (
                profile_key, profile_name, enabled_blocks_json,
                block_weights_json, is_builtin
            )
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(profile_key) DO UPDATE SET
                profile_name = excluded.profile_name,
                enabled_blocks_json = excluded.enabled_blocks_json,
                block_weights_json = excluded.block_weights_json,
                is_builtin = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                profile.key,
                profile.name,
                json.dumps(enabled_blocks, sort_keys=True),
                json.dumps(weights, sort_keys=True),
            ),
        )

    def save_price_value(
        self,
        listing: ListingForScoring,
        result: PriceValueResult,
        calculated_at: str,
    ) -> None:
        price_per_private_room = None
        if (
            listing.price_eur is not None
            and listing.effective_private_rooms is not None
            and listing.effective_private_rooms > 0
        ):
            price_per_private_room = listing.price_eur / listing.effective_private_rooms
        baseline = result.baseline
        self.connection.execute(
            """
            INSERT INTO price_value_analyses (
                listing_id, price_per_effective_private_room, price_value_score,
                price_per_m2_score, relative_market_score, absolute_price_score,
                suspicious_low_price_flag, market_baseline_level_used,
                market_baseline_sample_size, market_baseline_median_price_per_m2,
                market_baseline_explanation, calculated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                price_per_effective_private_room = excluded.price_per_effective_private_room,
                price_value_score = excluded.price_value_score,
                price_per_m2_score = excluded.price_per_m2_score,
                relative_market_score = excluded.relative_market_score,
                suspicious_low_price_flag = excluded.suspicious_low_price_flag,
                market_baseline_level_used = excluded.market_baseline_level_used,
                market_baseline_sample_size = excluded.market_baseline_sample_size,
                market_baseline_median_price_per_m2 = excluded.market_baseline_median_price_per_m2,
                market_baseline_explanation = excluded.market_baseline_explanation,
                calculated_at = excluded.calculated_at
            """,
            (
                listing.listing_id,
                price_per_private_room,
                result.relative_market_score,
                result.price_per_m2_score,
                result.relative_market_score,
                1 if result.suspicious_low_price_flag else 0,
                None if baseline is None else baseline.level.value,
                None if baseline is None else baseline.sample_size,
                None if baseline is None else baseline.median_price_per_m2,
                None if baseline is None else baseline.explanation,
                calculated_at,
            ),
        )

    def save_score_result(self, listing_id: int, result: ScoreResult, calculated_at: str) -> None:
        breakdown = {
            block.block_key.value: {
                "score": block.score,
                "explanation": block.explanation,
            }
            for block in result.block_scores
        }
        self.connection.execute(
            """
            INSERT INTO score_results (
                listing_id, profile_key, overall_score, score_breakdown_json,
                score_explanation, tie_breaker_explanation, calculated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(listing_id, profile_key) DO UPDATE SET
                overall_score = excluded.overall_score,
                score_breakdown_json = excluded.score_breakdown_json,
                score_explanation = excluded.score_explanation,
                tie_breaker_explanation = excluded.tie_breaker_explanation,
                calculated_at = excluded.calculated_at
            """,
            (
                listing_id,
                result.profile_key,
                result.overall_score,
                json.dumps(breakdown, ensure_ascii=False, sort_keys=True),
                result.explanation,
                calculated_at,
            ),
        )


def _listing_for_scoring(row: sqlite3.Row) -> ListingForScoring:
    return ListingForScoring(
        listing_id=row["listing_id"],
        price_eur=row["price_eur"],
        area_m2=row["area_m2"],
        district=row["district"],
        declared_rooms_ss=row["declared_rooms_ss"],
        building_series=row["building_series"],
        effective_private_rooms=row["effective_private_rooms"],
        layout_confidence_label=_layout_confidence(row["layout_confidence_label"]),
        mortgage_risk_level=_mortgage_risk(row["mortgage_risk_level"]),
        rtu_score=row["rtu_score"],
        transport_score=row["transport_score"],
        station_score=row["station_score"],
        shop_score=row["shop_score"],
        listing_status=row["listing_status"],
    )


def _layout_confidence(value: str | None) -> LayoutConfidenceLabel | None:
    try:
        return None if value is None else LayoutConfidenceLabel(value)
    except ValueError:
        return None


def _mortgage_risk(value: str | None) -> MortgageRiskLevel | None:
    try:
        return None if value is None else MortgageRiskLevel(value)
    except ValueError:
        return None
