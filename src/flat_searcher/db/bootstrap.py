"""SQLite bootstrap helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class DatabaseInitResult:
    database_path: Path
    schema_version: str


def init_database(database_path: Path) -> DatabaseInitResult:
    database_path = database_path.expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema_sql)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", "001"),
        )
        schema_version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            ("schema_version",),
        ).fetchone()[0]
        connection.commit()

    return DatabaseInitResult(database_path=database_path, schema_version=schema_version)
