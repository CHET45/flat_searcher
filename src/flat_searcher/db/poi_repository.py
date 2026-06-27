"""Persistence for cached OSM infrastructure around listings."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from flat_searcher.geo import Coordinate, NearbyPOI, POICategory


@dataclass(frozen=True)
class POIFetchState:
    listing_id: int
    latitude: float
    longitude: float
    radius_m: int
    fetched_at: str
    source_endpoint: str


@dataclass(frozen=True)
class InfrastructureSummary:
    has_cache: bool
    nearest_shop_distance_m: float | None
    shops_within_300m: int
    shops_within_700m: int
    shops_within_1200m: int
    nearest_transport_stop_distance_m: float | None
    transport_stops_within_900m: int


class POICacheRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_fetch_state(self, listing_id: int) -> POIFetchState | None:
        row = self.connection.execute(
            """
            SELECT listing_id, latitude, longitude, radius_m, fetched_at,
                   source_endpoint
            FROM osm_poi_fetches
            WHERE listing_id = ?
            """,
            (listing_id,),
        ).fetchone()
        if row is None:
            return None
        return POIFetchState(
            listing_id=row["listing_id"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            radius_m=row["radius_m"],
            fetched_at=row["fetched_at"],
            source_endpoint=row["source_endpoint"],
        )

    def replace_listing_pois(
        self,
        listing_id: int,
        origin: Coordinate,
        radius_m: int,
        fetched_at: str,
        source_endpoint: str,
        pois_with_distance: tuple[tuple[NearbyPOI, float], ...],
    ) -> None:
        self.connection.execute(
            "DELETE FROM osm_listing_pois WHERE listing_id = ?",
            (listing_id,),
        )
        for poi, distance_m in pois_with_distance:
            self.connection.execute(
                """
                INSERT INTO osm_pois (
                    osm_element_type, osm_element_id, category, name,
                    latitude, longitude, tags_json, fetched_at, source_endpoint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(osm_element_type, osm_element_id, category) DO UPDATE SET
                    name = excluded.name,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    tags_json = excluded.tags_json,
                    fetched_at = excluded.fetched_at,
                    source_endpoint = excluded.source_endpoint
                """,
                (
                    poi.osm_element_type,
                    poi.osm_element_id,
                    poi.category.value,
                    poi.name,
                    poi.coordinate.latitude,
                    poi.coordinate.longitude,
                    json.dumps(poi.tags, ensure_ascii=False, sort_keys=True),
                    fetched_at,
                    source_endpoint,
                ),
            )
            poi_id = self.connection.execute(
                """
                SELECT id
                FROM osm_pois
                WHERE osm_element_type = ?
                  AND osm_element_id = ?
                  AND category = ?
                """,
                (
                    poi.osm_element_type,
                    poi.osm_element_id,
                    poi.category.value,
                ),
            ).fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO osm_listing_pois (listing_id, poi_id, distance_m)
                VALUES (?, ?, ?)
                """,
                (listing_id, poi_id, round(distance_m, 1)),
            )
        self.connection.execute(
            """
            INSERT INTO osm_poi_fetches (
                listing_id, latitude, longitude, radius_m,
                fetched_at, source_endpoint
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                radius_m = excluded.radius_m,
                fetched_at = excluded.fetched_at,
                source_endpoint = excluded.source_endpoint
            """,
            (
                listing_id,
                origin.latitude,
                origin.longitude,
                radius_m,
                fetched_at,
                source_endpoint,
            ),
        )
        self.connection.execute(
            """
            DELETE FROM osm_pois
            WHERE NOT EXISTS (
                SELECT 1
                FROM osm_listing_pois link
                WHERE link.poi_id = osm_pois.id
            )
            """
        )

    def load_infrastructure_summary(self, listing_id: int) -> InfrastructureSummary:
        has_cache = self.connection.execute(
            "SELECT 1 FROM osm_poi_fetches WHERE listing_id = ?",
            (listing_id,),
        ).fetchone() is not None
        rows = self.connection.execute(
            """
            SELECT poi.category, link.distance_m
            FROM osm_listing_pois link
            JOIN osm_pois poi ON poi.id = link.poi_id
            WHERE link.listing_id = ?
            """,
            (listing_id,),
        ).fetchall()
        shop_distances = [
            row["distance_m"]
            for row in rows
            if row["category"] == POICategory.GROCERY_SHOP.value
        ]
        transport_distances = [
            row["distance_m"]
            for row in rows
            if row["category"] == POICategory.TRANSPORT_STOP.value
        ]
        return InfrastructureSummary(
            has_cache=has_cache,
            nearest_shop_distance_m=min(shop_distances, default=None),
            shops_within_300m=_count_within(shop_distances, 300),
            shops_within_700m=_count_within(shop_distances, 700),
            shops_within_1200m=_count_within(shop_distances, 1_200),
            nearest_transport_stop_distance_m=min(
                transport_distances,
                default=None,
            ),
            transport_stops_within_900m=_count_within(
                transport_distances,
                900,
            ),
        )


def _count_within(distances: list[float], radius_m: float) -> int:
    return sum(distance <= radius_m for distance in distances)
