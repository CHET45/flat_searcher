"""Idempotent SQLite schema migrations."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = "002"


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
