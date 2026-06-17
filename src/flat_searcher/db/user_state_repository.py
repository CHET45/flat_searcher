"""Repository for user listing workflow state."""

from __future__ import annotations

import sqlite3


class UserStateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def mark_viewed(self, listing_id: int, opened_at: str) -> None:
        self._ensure_state(listing_id)
        self.connection.execute(
            """
            UPDATE user_listing_states
            SET is_viewed = 1,
                last_user_opened_at = ?,
                user_status = CASE
                    WHEN is_favorite = 1 THEN user_status
                    WHEN is_rejected = 1 THEN user_status
                    ELSE 'viewed'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE listing_id = ?
            """,
            (opened_at, listing_id),
        )

    def set_favorite(self, listing_id: int, is_favorite: bool) -> None:
        self._ensure_state(listing_id)
        self.connection.execute(
            """
            UPDATE user_listing_states
            SET is_favorite = ?,
                user_status = CASE
                    WHEN ? = 1 THEN 'favorite'
                    WHEN is_rejected = 1 THEN 'rejected'
                    WHEN is_viewed = 1 THEN 'viewed'
                    ELSE 'unseen'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE listing_id = ?
            """,
            (1 if is_favorite else 0, 1 if is_favorite else 0, listing_id),
        )

    def set_rejected(self, listing_id: int, is_rejected: bool) -> None:
        self._ensure_state(listing_id)
        self.connection.execute(
            """
            UPDATE user_listing_states
            SET is_rejected = ?,
                user_status = CASE
                    WHEN ? = 1 THEN 'rejected'
                    WHEN is_favorite = 1 THEN 'favorite'
                    WHEN is_viewed = 1 THEN 'viewed'
                    ELSE 'unseen'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE listing_id = ?
            """,
            (1 if is_rejected else 0, 1 if is_rejected else 0, listing_id),
        )

    def update_notes(self, listing_id: int, notes: str | None) -> None:
        self._ensure_state(listing_id)
        self.connection.execute(
            """
            UPDATE user_listing_states
            SET user_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE listing_id = ?
            """,
            (notes, listing_id),
        )

    def get_state(self, listing_id: int) -> sqlite3.Row:
        self._ensure_state(listing_id)
        return self.connection.execute(
            "SELECT * FROM user_listing_states WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()

    def _ensure_state(self, listing_id: int) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO user_listing_states (listing_id, user_status)
            VALUES (?, 'unseen')
            """,
            (listing_id,),
        )
