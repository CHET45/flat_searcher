"""Idempotent SQLite schema migrations."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = "003"


def apply_migrations(connection: sqlite3.Connection) -> None:
    _ensure_columns(
        connection,
        "listings",
        {
            "needs_ai_analysis": "INTEGER NOT NULL DEFAULT 0",
            "listing_title": "TEXT",
            "listing_summary_text": "TEXT",
            "listing_table_metadata_json": "TEXT",
            "detail_fields_json": "TEXT",
        },
    )
    _ensure_latest_ai_view(connection)
    _ensure_osm_poi_tables(connection)


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing_columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def _ensure_latest_ai_view(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIEW IF NOT EXISTS latest_ai_analyses AS
        SELECT ai.*
        FROM ai_analyses ai
        JOIN (
            SELECT listing_id, MAX(id) AS latest_id
            FROM ai_analyses
            WHERE status = 'finished'
            GROUP BY listing_id
        ) latest ON latest.latest_id = ai.id
        """
    )


def _ensure_osm_poi_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS osm_pois (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_element_type TEXT NOT NULL,
            osm_element_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            name TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            tags_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_endpoint TEXT NOT NULL,
            UNIQUE(osm_element_type, osm_element_id, category)
        );

        CREATE INDEX IF NOT EXISTS idx_osm_pois_category
        ON osm_pois (category);

        CREATE TABLE IF NOT EXISTS osm_listing_pois (
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            poi_id INTEGER NOT NULL REFERENCES osm_pois(id) ON DELETE CASCADE,
            distance_m REAL NOT NULL,
            PRIMARY KEY(listing_id, poi_id)
        );

        CREATE INDEX IF NOT EXISTS idx_osm_listing_pois_listing_distance
        ON osm_listing_pois (listing_id, distance_m);

        CREATE TABLE IF NOT EXISTS osm_poi_fetches (
            listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            source_endpoint TEXT NOT NULL
        );
        """
    )
