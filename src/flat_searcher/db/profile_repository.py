"""Persistence for built-in and custom scoring profiles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from flat_searcher.scoring import (
    ImportanceLevel,
    ScoreBlockKey,
    ScoringProfile,
    builtin_profiles,
)


@dataclass(frozen=True)
class ProfileSummary:
    profile_key: str
    profile_name: str
    is_builtin: bool
    base_profile_key: str | None


class ProfileRepository:
    """Stores scoring profiles so the UI can switch search strategies.

    Built-in profiles are reseeded from code on every sync so improvements ship
    with the application. Custom profiles are user data and are never overwritten
    by the seeding step.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def sync_builtin_profiles(self) -> None:
        for profile in builtin_profiles().values():
            self.save_profile(profile)

    def save_profile(self, profile: ScoringProfile) -> None:
        weights = {
            block_key.value: importance.value
            for block_key, importance in profile.block_importance.items()
        }
        enabled_blocks = [
            block_key.value
            for block_key, importance in profile.block_importance.items()
            if importance.weight > 0
        ]
        self.connection.execute(
            """
            INSERT INTO scoring_profiles (
                profile_key, profile_name, base_profile_key,
                enabled_blocks_json, block_weights_json, is_builtin
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_key) DO UPDATE SET
                profile_name = excluded.profile_name,
                base_profile_key = excluded.base_profile_key,
                enabled_blocks_json = excluded.enabled_blocks_json,
                block_weights_json = excluded.block_weights_json,
                is_builtin = excluded.is_builtin,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                profile.key,
                profile.name,
                profile.base_profile_key,
                json.dumps(enabled_blocks, sort_keys=True),
                json.dumps(weights, sort_keys=True),
                1 if profile.is_builtin else 0,
            ),
        )

    def load_profile(self, profile_key: str) -> ScoringProfile | None:
        row = self.connection.execute(
            """
            SELECT profile_key, profile_name, base_profile_key,
                   block_weights_json, is_builtin
            FROM scoring_profiles
            WHERE profile_key = ?
            """,
            (profile_key,),
        ).fetchone()
        if row is None:
            return builtin_profiles().get(profile_key)
        return _profile_from_row(row)

    def list_profiles(self) -> tuple[ProfileSummary, ...]:
        rows = self.connection.execute(
            """
            SELECT profile_key, profile_name, base_profile_key, is_builtin
            FROM scoring_profiles
            ORDER BY is_builtin DESC, profile_name
            """
        ).fetchall()
        return tuple(
            ProfileSummary(
                profile_key=row["profile_key"],
                profile_name=row["profile_name"],
                is_builtin=bool(row["is_builtin"]),
                base_profile_key=row["base_profile_key"],
            )
            for row in rows
        )

    def delete_profile(self, profile_key: str) -> None:
        """Delete a custom profile and any cached scores tied to it."""

        self.connection.execute(
            "DELETE FROM scoring_profiles WHERE profile_key = ? AND is_builtin = 0",
            (profile_key,),
        )
        self.connection.execute(
            "DELETE FROM score_results WHERE profile_key = ?",
            (profile_key,),
        )

    def rename_profile(self, profile_key: str, profile_name: str) -> bool:
        """Rename a custom profile and leave built-in presets untouched."""

        result = self.connection.execute(
            """
            UPDATE scoring_profiles
            SET profile_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE profile_key = ? AND is_builtin = 0
            """,
            (profile_name, profile_key),
        )
        return result.rowcount > 0


def _profile_from_row(row: sqlite3.Row) -> ScoringProfile:
    weights: dict[str, str] = json.loads(row["block_weights_json"] or "{}")
    block_importance: dict[ScoreBlockKey, ImportanceLevel] = {}
    for block in ScoreBlockKey:
        raw = weights.get(block.value)
        block_importance[block] = (
            _parse_importance(raw) if raw is not None else ImportanceLevel.IGNORE
        )
    return ScoringProfile(
        key=row["profile_key"],
        name=row["profile_name"],
        block_importance=block_importance,
        base_profile_key=row["base_profile_key"],
        is_builtin=bool(row["is_builtin"]),
    )


def _parse_importance(value: str) -> ImportanceLevel:
    try:
        return ImportanceLevel(value)
    except ValueError:
        return ImportanceLevel.IGNORE
