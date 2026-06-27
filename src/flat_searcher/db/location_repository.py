"""Persistence for location score calculation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class GeocodedListing:
    listing_id: int
    latitude: float | None
    longitude: float | None
    geo_scores_enabled: bool
    disabled_reason: str | None


class LocationScoreRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_geocoded_listings(self) -> tuple[GeocodedListing, ...]:
        rows = self.connection.execute(
            """
            SELECT l.id AS listing_id, g.latitude, g.longitude,
                   g.geo_scores_enabled, g.geo_scores_disabled_reason
            FROM listings l
            JOIN geocoding_results g ON g.listing_id = l.id
            WHERE l.listing_status = 'active'
            ORDER BY l.id
            """
        ).fetchall()
        return tuple(
            GeocodedListing(
                listing_id=row["listing_id"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                geo_scores_enabled=bool(row["geo_scores_enabled"]),
                disabled_reason=row["geo_scores_disabled_reason"],
            )
            for row in rows
        )

    def save_location_scores(
        self,
        listing_id: int,
        distance_to_rtu_m: float | None,
        rtu_score: float | None,
        distance_to_station_m: float | None,
        station_score: float | None,
        nearest_shop_distance_m: float | None,
        shops_within_300m: int | None,
        shops_within_700m: int | None,
        shops_within_1200m: int | None,
        shop_score: float | None,
        nearest_transport_stop_distance_m: float | None,
        transport_stops_nearby_count: int | None,
        transport_score: float | None,
        calculated_at: str,
        explanation: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO location_scores (
                listing_id, distance_to_rtu_m, rtu_score,
                distance_to_central_station_m, station_score,
                nearest_shop_distance_m, shops_within_300m,
                shops_within_700m, shops_within_1200m, shop_score,
                nearest_transport_stop_distance_m,
                transport_stops_nearby_count, transport_score,
                calculated_at, explanation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                distance_to_rtu_m = excluded.distance_to_rtu_m,
                rtu_score = excluded.rtu_score,
                distance_to_central_station_m = excluded.distance_to_central_station_m,
                station_score = excluded.station_score,
                nearest_shop_distance_m = excluded.nearest_shop_distance_m,
                shops_within_300m = excluded.shops_within_300m,
                shops_within_700m = excluded.shops_within_700m,
                shops_within_1200m = excluded.shops_within_1200m,
                shop_score = excluded.shop_score,
                nearest_transport_stop_distance_m =
                    excluded.nearest_transport_stop_distance_m,
                transport_stops_nearby_count =
                    excluded.transport_stops_nearby_count,
                transport_score = excluded.transport_score,
                calculated_at = excluded.calculated_at,
                explanation = excluded.explanation
            """,
            (
                listing_id,
                distance_to_rtu_m,
                rtu_score,
                distance_to_station_m,
                station_score,
                nearest_shop_distance_m,
                shops_within_300m,
                shops_within_700m,
                shops_within_1200m,
                shop_score,
                nearest_transport_stop_distance_m,
                transport_stops_nearby_count,
                transport_score,
                calculated_at,
                explanation,
            ),
        )
