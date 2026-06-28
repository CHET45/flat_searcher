"""Persistence for saved search sessions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from flat_searcher.filtering import ListingFilters, filters_from_dict, filters_to_dict


@dataclass(frozen=True)
class SearchSessionSummary:
    session_id: int
    session_name: str
    selected_profile_key: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SearchSession:
    session_id: int
    session_name: str
    selected_profile_key: str | None
    filters: ListingFilters
    sort_mode: str | None
    hidden_statuses: tuple[str, ...] = field(default_factory=tuple)


class SearchSessionRepository:
    """Stores a user's profile, filters and sort mode so they can resume later."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_session(
        self,
        session_name: str,
        selected_profile_key: str | None,
        filters: ListingFilters,
        sort_mode: str | None = None,
        hidden_statuses: tuple[str, ...] = (),
    ) -> int:
        """Insert a new session, or update an existing one with the same name."""

        existing = self.connection.execute(
            "SELECT id FROM search_sessions WHERE session_name = ?",
            (session_name,),
        ).fetchone()
        filters_json = json.dumps(filters_to_dict(filters), sort_keys=True)
        hidden_json = json.dumps(list(hidden_statuses), sort_keys=True)
        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO search_sessions (
                    session_name, selected_profile_key, filters_json,
                    sort_mode, hidden_statuses_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_name, selected_profile_key, filters_json, sort_mode, hidden_json),
            )
            return int(cursor.lastrowid)
        self.connection.execute(
            """
            UPDATE search_sessions
            SET selected_profile_key = ?, filters_json = ?, sort_mode = ?,
                hidden_statuses_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (selected_profile_key, filters_json, sort_mode, hidden_json, existing["id"]),
        )
        return int(existing["id"])

    def list_sessions(self) -> tuple[SearchSessionSummary, ...]:
        rows = self.connection.execute(
            """
            SELECT id, session_name, selected_profile_key, created_at, updated_at
            FROM search_sessions
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        return tuple(
            SearchSessionSummary(
                session_id=row["id"],
                session_name=row["session_name"],
                selected_profile_key=row["selected_profile_key"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    def load_session(self, session_id: int) -> SearchSession | None:
        row = self.connection.execute(
            """
            SELECT id, session_name, selected_profile_key, filters_json,
                   sort_mode, hidden_statuses_json
            FROM search_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        filters = filters_from_dict(json.loads(row["filters_json"] or "{}"))
        hidden = tuple(json.loads(row["hidden_statuses_json"] or "[]"))
        return SearchSession(
            session_id=row["id"],
            session_name=row["session_name"],
            selected_profile_key=row["selected_profile_key"],
            filters=filters,
            sort_mode=row["sort_mode"],
            hidden_statuses=hidden,
        )

    def delete_session(self, session_id: int) -> None:
        self.connection.execute(
            "DELETE FROM search_sessions WHERE id = ?",
            (session_id,),
        )
