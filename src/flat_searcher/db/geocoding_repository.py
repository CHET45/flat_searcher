"""Persistence helpers for geocoding work."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flat_searcher.geo import AddressPrecision, GeocodeConfidence


@dataclass(frozen=True)
class ListingAddressRecord:
    listing_id: int
    district: str | None
    street: str | None
    house_number: str | None


class GeocodingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_ungeocoded_addresses(self, limit: int | None = None) -> tuple[ListingAddressRecord, ...]:
        query = """
            SELECT l.id, l.district, l.street, l.house_number
            FROM listings l
            LEFT JOIN geocoding_results g ON g.listing_id = l.id
            WHERE g.id IS NULL
              AND (l.district IS NOT NULL OR l.street IS NOT NULL)
            ORDER BY l.id
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.connection.execute(query, params).fetchall()
        return tuple(
            ListingAddressRecord(
                listing_id=row["id"],
                district=row["district"],
                street=row["street"],
                house_number=row["house_number"],
            )
            for row in rows
        )

    def upsert_geocoding_result(
        self,
        listing_id: int,
        normalized_address: str,
        latitude: float | None,
        longitude: float | None,
        precision: AddressPrecision,
        confidence: GeocodeConfidence | None,
        source: str,
        explanation: str,
        geo_scores_enabled: bool,
        disabled_reason: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO geocoding_results (
                listing_id, normalized_address, latitude, longitude,
                geocode_precision, geocode_confidence, geocode_source,
                geocode_explanation, geo_scores_enabled, geo_scores_disabled_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                normalized_address = excluded.normalized_address,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                geocode_precision = excluded.geocode_precision,
                geocode_confidence = excluded.geocode_confidence,
                geocode_source = excluded.geocode_source,
                geocode_explanation = excluded.geocode_explanation,
                geo_scores_enabled = excluded.geo_scores_enabled,
                geo_scores_disabled_reason = excluded.geo_scores_disabled_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                listing_id,
                normalized_address,
                latitude,
                longitude,
                precision.value,
                None if confidence is None else confidence.value,
                source,
                explanation,
                1 if geo_scores_enabled else 0,
                disabled_reason,
            ),
        )
