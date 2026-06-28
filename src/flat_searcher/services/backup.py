"""Consistent SQLite database backup/export.

Uses the SQLite online backup API so the copy is transactionally consistent even
if the database is being used. This protects analyzed listings, scores and user
workflow state across app updates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    source_path: Path
    backup_path: Path
    size_bytes: int


def backup_database(database_path: Path, output: Path | None = None) -> BackupResult:
    database_path = database_path.expanduser()
    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")

    backup_path = _resolve_backup_path(database_path, output)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(database_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    return BackupResult(
        source_path=database_path,
        backup_path=backup_path,
        size_bytes=backup_path.stat().st_size,
    )


def _resolve_backup_path(database_path: Path, output: Path | None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    default_name = f"{database_path.stem}-backup-{timestamp}.sqlite3"
    if output is None:
        return database_path.parent / default_name
    output = output.expanduser()
    if output.exists() and output.is_dir():
        return output / default_name
    if output.suffix == "":
        return output / default_name
    return output
