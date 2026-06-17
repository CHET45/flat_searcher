"""Read repository for UI-ready listing data."""

from __future__ import annotations

import sqlite3

from flat_searcher.db.read_models import (
    ListingChangeEvent,
    ListingDetailReadModel,
    ListingHistorySnapshot,
    parse_address_precision,
    parse_geocode_confidence,
    parse_layout_confidence,
    parse_mortgage_risk,
)
from flat_searcher.filtering import ListingCandidate
from flat_searcher.geo import AddressPrecision
from flat_searcher.mapping import MapApartmentPoint


class ListingReadRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_candidates(self, profile_key: str) -> tuple[ListingCandidate, ...]:
        rows = self.connection.execute(
            """
            SELECT
                l.id AS listing_id,
                l.listing_status,
                l.district,
                l.street,
                l.price_eur,
                l.area_m2,
                l.declared_rooms_ss,
                COALESCE(u.user_status, 'unseen') AS user_status,
                COALESCE(u.is_favorite, 0) AS is_favorite,
                COALESCE(u.is_rejected, 0) AS is_rejected,
                COALESCE(u.is_viewed, 0) AS is_viewed,
                a.effective_private_rooms,
                a.ss_vs_ai_room_conflict,
                a.kitchen_living_detected,
                a.layout_confidence_label,
                a.mortgage_risk_level,
                a.stove_heating_risk,
                a.wooden_building_risk,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM listing_images li
                        WHERE li.listing_id = l.id AND li.is_floor_plan = 1
                    ) THEN 1
                    WHEN a.floor_plan_image_ids IS NOT NULL AND a.floor_plan_image_ids != '' THEN 1
                    ELSE 0
                END AS has_floor_plan,
                ls.rtu_score,
                ls.transport_score,
                ls.station_score,
                sr.overall_score
            FROM listings l
            LEFT JOIN user_listing_states u ON u.listing_id = l.id
            LEFT JOIN latest_ai_analyses a ON a.listing_id = l.id
            LEFT JOIN location_scores ls ON ls.listing_id = l.id
            LEFT JOIN score_results sr ON sr.listing_id = l.id AND sr.profile_key = ?
            ORDER BY l.id
            """,
            (profile_key,),
        ).fetchall()
        return tuple(_candidate_from_row(row) for row in rows)

    def load_map_points(self, profile_key: str) -> tuple[MapApartmentPoint, ...]:
        rows = self.connection.execute(
            """
            SELECT
                l.id AS listing_id,
                l.listing_status,
                COALESCE(u.is_favorite, 0) AS is_favorite,
                COALESCE(u.is_rejected, 0) AS is_rejected,
                g.latitude,
                g.longitude,
                g.geocode_precision,
                sr.overall_score
            FROM listings l
            LEFT JOIN user_listing_states u ON u.listing_id = l.id
            LEFT JOIN geocoding_results g ON g.listing_id = l.id
            LEFT JOIN score_results sr ON sr.listing_id = l.id AND sr.profile_key = ?
            ORDER BY l.id
            """,
            (profile_key,),
        ).fetchall()
        return tuple(_map_point_from_row(row) for row in rows)

    def load_detail(self, listing_id: int, profile_key: str) -> ListingDetailReadModel | None:
        row = self.connection.execute(
            """
            SELECT
                l.*,
                COALESCE(u.user_status, 'unseen') AS user_status,
                COALESCE(u.is_favorite, 0) AS is_favorite,
                COALESCE(u.is_rejected, 0) AS is_rejected,
                COALESCE(u.is_viewed, 0) AS is_viewed,
                u.user_notes,
                a.effective_private_rooms,
                a.walkthrough_rooms,
                a.kitchen_living_detected,
                a.layout_confidence_label,
                a.layout_explanation_user,
                a.mortgage_risk_level,
                a.mortgage_risk_reasons,
                a.mortgage_explanation_user,
                g.latitude,
                g.longitude,
                g.geocode_precision,
                g.geocode_confidence,
                g.geo_scores_enabled,
                g.geo_scores_disabled_reason,
                sr.overall_score
            FROM listings l
            LEFT JOIN user_listing_states u ON u.listing_id = l.id
            LEFT JOIN latest_ai_analyses a ON a.listing_id = l.id
            LEFT JOIN geocoding_results g ON g.listing_id = l.id
            LEFT JOIN score_results sr ON sr.listing_id = l.id AND sr.profile_key = ?
            WHERE l.id = ?
            """,
            (profile_key, listing_id),
        ).fetchone()
        if row is None:
            return None
        return _detail_from_row(
            row,
            history_snapshots=self._load_history_snapshots(listing_id),
            change_events=self._load_change_events(listing_id),
        )

    def _load_history_snapshots(self, listing_id: int) -> tuple[ListingHistorySnapshot, ...]:
        rows = self.connection.execute(
            """
            SELECT checked_at, price_eur, unique_visits, description_hash,
                   images_count, is_active, raw_snapshot_hash
            FROM listing_snapshots
            WHERE listing_id = ?
            ORDER BY checked_at DESC
            """,
            (listing_id,),
        ).fetchall()
        return tuple(
            ListingHistorySnapshot(
                checked_at=row["checked_at"],
                price_eur=row["price_eur"],
                unique_visits=row["unique_visits"],
                description_hash=row["description_hash"],
                images_count=row["images_count"],
                is_active=bool(row["is_active"]),
                raw_snapshot_hash=row["raw_snapshot_hash"],
            )
            for row in rows
        )

    def _load_change_events(self, listing_id: int) -> tuple[ListingChangeEvent, ...]:
        rows = self.connection.execute(
            """
            SELECT detected_at, event_type, old_value, new_value, delta_value, explanation
            FROM listing_change_events
            WHERE listing_id = ?
            ORDER BY detected_at DESC, id DESC
            """,
            (listing_id,),
        ).fetchall()
        return tuple(
            ListingChangeEvent(
                detected_at=row["detected_at"],
                event_type=row["event_type"],
                old_value=row["old_value"],
                new_value=row["new_value"],
                delta_value=row["delta_value"],
                explanation=row["explanation"],
            )
            for row in rows
        )


def _candidate_from_row(row: sqlite3.Row) -> ListingCandidate:
    return ListingCandidate(
        listing_id=row["listing_id"],
        score=row["overall_score"],
        listing_status=row["listing_status"],
        user_status=row["user_status"],
        is_favorite=bool(row["is_favorite"]),
        is_rejected=bool(row["is_rejected"]),
        is_viewed=bool(row["is_viewed"]),
        district=row["district"],
        street=row["street"],
        price_eur=row["price_eur"],
        area_m2=row["area_m2"],
        declared_rooms_ss=row["declared_rooms_ss"],
        effective_private_rooms=row["effective_private_rooms"],
        room_conflict=bool(row["ss_vs_ai_room_conflict"]),
        kitchen_living_detected=bool(row["kitchen_living_detected"]),
        layout_confidence_label=parse_layout_confidence(row["layout_confidence_label"]),
        mortgage_risk_level=parse_mortgage_risk(row["mortgage_risk_level"]),
        stove_heating_risk=bool(row["stove_heating_risk"]),
        wooden_building_risk=bool(row["wooden_building_risk"]),
        has_floor_plan=bool(row["has_floor_plan"]),
        rtu_score=row["rtu_score"],
        transport_score=row["transport_score"],
        station_score=row["station_score"],
    )


def _map_point_from_row(row: sqlite3.Row) -> MapApartmentPoint:
    precision = parse_address_precision(row["geocode_precision"]) or AddressPrecision.UNKNOWN
    return MapApartmentPoint(
        listing_id=row["listing_id"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        address_precision=precision,
        score=row["overall_score"],
        is_favorite=bool(row["is_favorite"]),
        is_rejected=bool(row["is_rejected"]),
        listing_status=row["listing_status"],
    )


def _detail_from_row(
    row: sqlite3.Row,
    history_snapshots: tuple[ListingHistorySnapshot, ...],
    change_events: tuple[ListingChangeEvent, ...],
) -> ListingDetailReadModel:
    return ListingDetailReadModel(
        listing_id=row["id"],
        ss_id=row["ss_id"],
        ss_url=row["ss_url"],
        listing_status=row["listing_status"],
        user_status=row["user_status"],
        is_favorite=bool(row["is_favorite"]),
        is_rejected=bool(row["is_rejected"]),
        is_viewed=bool(row["is_viewed"]),
        user_notes=row["user_notes"],
        district=row["district"],
        street=row["street"],
        house_number=row["house_number"],
        address_raw=row["address_raw"],
        price_eur=row["price_eur"],
        price_per_m2=row["price_per_m2"],
        area_m2=row["area_m2"],
        declared_rooms_ss=row["declared_rooms_ss"],
        floor=row["floor"],
        total_floors=row["total_floors"],
        building_series=row["building_series"],
        building_type=row["building_type"],
        listing_date_text=row["listing_date_text"],
        unique_visits=row["unique_visits"],
        description_text=row["description_text"],
        effective_private_rooms=row["effective_private_rooms"],
        walkthrough_rooms=row["walkthrough_rooms"],
        kitchen_living_detected=bool(row["kitchen_living_detected"]),
        layout_confidence_label=parse_layout_confidence(row["layout_confidence_label"]),
        layout_explanation_user=row["layout_explanation_user"],
        mortgage_risk_level=parse_mortgage_risk(row["mortgage_risk_level"]),
        mortgage_risk_reasons=row["mortgage_risk_reasons"],
        mortgage_explanation_user=row["mortgage_explanation_user"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        geocode_precision=parse_address_precision(row["geocode_precision"]),
        geocode_confidence=parse_geocode_confidence(row["geocode_confidence"]),
        geo_scores_enabled=bool(row["geo_scores_enabled"]),
        geo_scores_disabled_reason=row["geo_scores_disabled_reason"],
        overall_score=row["overall_score"],
        history_snapshots=history_snapshots,
        change_events=change_events,
    )
