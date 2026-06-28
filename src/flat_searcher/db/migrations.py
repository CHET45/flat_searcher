"""Idempotent SQLite schema migrations.

`schema.sql` recreates every table, index and view with `IF NOT EXISTS` on each
init, so migrations only handle changes that statement cannot express: adding
columns to an existing table and dropping objects that were removed from the
schema.
"""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = "006"

_REMOVED_COLUMNS = {
    "scoring_profiles": ("block_settings_json",),
    "score_results": ("tie_breaker_explanation",),
}


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
    for table_name, columns in _REMOVED_COLUMNS.items():
        _drop_columns(connection, table_name, columns)


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing_columns = _column_names(connection, table_name)
    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def _drop_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    existing_columns = _column_names(connection, table_name)
    for column_name in columns:
        if column_name in existing_columns:
            connection.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
