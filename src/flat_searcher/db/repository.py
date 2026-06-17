"""Repository layer for listings and sync history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from flat_searcher.models import ListingPayload
from flat_searcher.scraper.parsing import stable_hash


@dataclass(frozen=True)
class ListingUpsertResult:
    listing_id: int
    is_new: bool
    change_events: tuple[str, ...]


@contextmanager
def open_database(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class ListingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_app_run(self, run_type: str, started_at: str) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO app_runs (started_at, run_type, status)
            VALUES (?, ?, ?)
            """,
            (started_at, run_type, "running"),
        )
        return int(cursor.lastrowid)

    def finish_app_run(self, app_run_id: int, finished_at: str, status: str, message: str) -> None:
        self.connection.execute(
            """
            UPDATE app_runs
            SET finished_at = ?, status = ?, message = ?
            WHERE id = ?
            """,
            (finished_at, status, message, app_run_id),
        )

    def upsert_listing(
        self,
        payload: ListingPayload,
        app_run_id: int,
        checked_at: str,
    ) -> ListingUpsertResult:
        existing = self._find_listing(payload.ss_id)
        description_hash = stable_hash(payload.description_text)
        raw_snapshot_hash = stable_hash(payload.raw_html)
        images_count = len(payload.image_urls) if payload.image_urls else None

        if existing is None:
            listing_id = self._insert_listing(
                payload,
                checked_at,
                description_hash,
                raw_snapshot_hash,
                images_count,
            )
            self._ensure_user_state(listing_id, "new")
            self._replace_listing_images(listing_id, payload.image_urls)
            self._insert_snapshot(
                listing_id,
                app_run_id,
                checked_at,
                payload,
                description_hash,
                raw_snapshot_hash,
                images_count,
                is_active=True,
            )
            return ListingUpsertResult(listing_id, True, ())

        listing_id = int(existing["id"])
        events = self._detect_change_events(existing, payload, description_hash, images_count)
        self._update_listing(
            listing_id,
            payload,
            checked_at,
            description_hash,
            raw_snapshot_hash,
            images_count,
            needs_ai_analysis=bool(events & {"description_changed", "image_count_changed"}),
        )
        if payload.raw_html is not None:
            self._replace_listing_images(listing_id, payload.image_urls)
        self._insert_snapshot(
            listing_id,
            app_run_id,
            checked_at,
            payload,
            description_hash,
            raw_snapshot_hash,
            images_count,
            is_active=True,
        )
        for event_type in sorted(events):
            self._insert_change_event(listing_id, checked_at, event_type, existing, payload)

        return ListingUpsertResult(listing_id, False, tuple(sorted(events)))

    def mark_missing_inactive(
        self,
        seen_ss_ids: set[str],
        app_run_id: int,
        checked_at: str,
    ) -> int:
        active_rows = self.connection.execute(
            "SELECT id, ss_id FROM listings WHERE listing_status = 'active'"
        ).fetchall()
        inactive_count = 0
        for row in active_rows:
            if row["ss_id"] in seen_ss_ids:
                continue
            listing_id = int(row["id"])
            self.connection.execute(
                """
                UPDATE listings
                SET listing_status = 'inactive',
                    became_inactive_at = ?,
                    last_checked_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (checked_at, checked_at, listing_id),
            )
            self.connection.execute(
                """
                INSERT INTO listing_snapshots (
                    listing_id, app_run_id, checked_at, is_active
                )
                VALUES (?, ?, ?, 0)
                """,
                (listing_id, app_run_id, checked_at),
            )
            self.connection.execute(
                """
                INSERT INTO listing_change_events (
                    listing_id, detected_at, event_type, explanation
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    listing_id,
                    checked_at,
                    "listing_became_inactive",
                    "Listing was not present in the latest full SS.com listing set.",
                ),
            )
            inactive_count += 1
        return inactive_count

    def count_listings(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0])

    def _find_listing(self, ss_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM listings WHERE ss_id = ?",
            (ss_id,),
        ).fetchone()

    def _insert_listing(
        self,
        payload: ListingPayload,
        checked_at: str,
        description_hash: str | None,
        raw_snapshot_hash: str | None,
        images_count: int | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO listings (
                ss_id, ss_url, listing_status, first_seen_at, last_seen_at,
                last_checked_at, is_new_since_last_run, needs_ai_analysis,
                listing_title, listing_summary_text, listing_table_metadata_json,
                detail_fields_json, address_raw, district, street, house_number,
                price_eur, price_per_m2, area_m2, declared_rooms_ss, floor,
                total_floors, building_series, building_type, listing_date_text,
                unique_visits, description_text, description_hash, images_count,
                raw_snapshot_hash, raw_text_snapshot
            )
            VALUES (
                ?, ?, 'active', ?, ?, ?, 1, 1,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            _listing_values(
                payload,
                checked_at,
                description_hash,
                raw_snapshot_hash,
                images_count,
                include_seen=True,
            ),
        )
        return int(cursor.lastrowid)

    def _update_listing(
        self,
        listing_id: int,
        payload: ListingPayload,
        checked_at: str,
        description_hash: str | None,
        raw_snapshot_hash: str | None,
        images_count: int | None,
        needs_ai_analysis: bool,
    ) -> None:
        existing = self.connection.execute(
            "SELECT * FROM listings WHERE id = ?",
            (listing_id,),
        ).fetchone()
        was_inactive = existing["listing_status"] == "inactive"
        has_detail = payload.raw_html is not None

        detail_fields_json = (
            json.dumps(payload.detail_fields, ensure_ascii=False, sort_keys=True)
            if has_detail
            else existing["detail_fields_json"]
        )
        description_text = payload.description_text if has_detail else existing["description_text"]
        description_hash_value = description_hash if has_detail else existing["description_hash"]
        images_count_value = images_count if has_detail else existing["images_count"]
        raw_snapshot_hash_value = raw_snapshot_hash if has_detail else existing["raw_snapshot_hash"]
        raw_text_snapshot = payload.raw_text_snapshot if has_detail else existing["raw_text_snapshot"]
        unique_visits = payload.unique_visits if has_detail else existing["unique_visits"]

        self.connection.execute(
            """
            UPDATE listings
            SET ss_url = ?,
                listing_status = 'active',
                last_seen_at = ?,
                last_checked_at = ?,
                reactivated_at = CASE WHEN ? THEN ? ELSE reactivated_at END,
                is_new_since_last_run = 0,
                needs_ai_analysis = CASE WHEN ? THEN 1 ELSE needs_ai_analysis END,
                listing_title = ?,
                listing_summary_text = ?,
                listing_table_metadata_json = ?,
                detail_fields_json = ?,
                address_raw = ?,
                district = ?,
                street = ?,
                house_number = ?,
                price_eur = ?,
                price_per_m2 = ?,
                area_m2 = ?,
                declared_rooms_ss = ?,
                floor = ?,
                total_floors = ?,
                building_series = ?,
                building_type = ?,
                listing_date_text = ?,
                unique_visits = ?,
                description_text = ?,
                description_hash = ?,
                images_count = ?,
                raw_snapshot_hash = ?,
                raw_text_snapshot = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.ss_url,
                checked_at,
                checked_at,
                1 if was_inactive else 0,
                checked_at,
                1 if needs_ai_analysis else 0,
                payload.listing_title,
                payload.listing_summary_text,
                json.dumps(payload.listing_table_metadata, ensure_ascii=False, sort_keys=True),
                detail_fields_json,
                payload.address_raw,
                payload.district,
                payload.street,
                payload.house_number,
                payload.price_eur,
                payload.price_per_m2,
                payload.area_m2,
                payload.declared_rooms_ss,
                payload.floor,
                payload.total_floors,
                payload.building_series,
                payload.building_type,
                payload.listing_date_text,
                unique_visits,
                description_text,
                description_hash_value,
                images_count_value,
                raw_snapshot_hash_value,
                raw_text_snapshot,
                listing_id,
            ),
        )
        if was_inactive:
            self.connection.execute(
                """
                INSERT INTO listing_change_events (
                    listing_id, detected_at, event_type, explanation
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    listing_id,
                    checked_at,
                    "listing_reactivated",
                    "Listing returned to the active SS.com listing set.",
                ),
            )

    def _insert_snapshot(
        self,
        listing_id: int,
        app_run_id: int,
        checked_at: str,
        payload: ListingPayload,
        description_hash: str | None,
        raw_snapshot_hash: str | None,
        images_count: int | None,
        is_active: bool,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO listing_snapshots (
                listing_id, app_run_id, checked_at, price_eur, unique_visits,
                description_hash, images_count, is_active, raw_snapshot_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                app_run_id,
                checked_at,
                payload.price_eur,
                payload.unique_visits,
                description_hash,
                images_count,
                1 if is_active else 0,
                raw_snapshot_hash,
            ),
        )

    def _replace_listing_images(self, listing_id: int, image_urls: tuple[str, ...]) -> None:
        self.connection.execute("DELETE FROM listing_images WHERE listing_id = ?", (listing_id,))
        self.connection.executemany(
            """
            INSERT INTO listing_images (listing_id, source_url)
            VALUES (?, ?)
            """,
            [(listing_id, image_url) for image_url in image_urls],
        )

    def _ensure_user_state(self, listing_id: int, user_status: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO user_listing_states (listing_id, user_status)
            VALUES (?, ?)
            """,
            (listing_id, user_status),
        )

    def _detect_change_events(
        self,
        existing: sqlite3.Row,
        payload: ListingPayload,
        description_hash: str | None,
        images_count: int | None,
    ) -> set[str]:
        events: set[str] = set()
        if existing["price_eur"] != payload.price_eur:
            events.add("price_changed")
        if payload.unique_visits is not None and existing["unique_visits"] != payload.unique_visits:
            events.add("unique_visits_changed")
        if payload.raw_html is not None:
            if existing["description_hash"] != description_hash:
                events.add("description_changed")
            if existing["images_count"] != images_count:
                events.add("image_count_changed")
        return events

    def _insert_change_event(
        self,
        listing_id: int,
        checked_at: str,
        event_type: str,
        existing: sqlite3.Row,
        payload: ListingPayload,
    ) -> None:
        old_value, new_value, delta_value = _event_values(event_type, existing, payload)
        self.connection.execute(
            """
            INSERT INTO listing_change_events (
                listing_id, detected_at, event_type, old_value, new_value, delta_value
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (listing_id, checked_at, event_type, old_value, new_value, delta_value),
        )


def _listing_values(
    payload: ListingPayload,
    checked_at: str,
    description_hash: str | None,
    raw_snapshot_hash: str | None,
    images_count: int | None,
    include_seen: bool,
) -> tuple[object, ...]:
    values: list[object] = [
        payload.ss_id,
        payload.ss_url,
    ]
    if include_seen:
        values.extend([checked_at, checked_at, checked_at])
    values.extend(
        [
            payload.listing_title,
            payload.listing_summary_text,
            json.dumps(payload.listing_table_metadata, ensure_ascii=False, sort_keys=True),
            json.dumps(payload.detail_fields, ensure_ascii=False, sort_keys=True),
            payload.address_raw,
            payload.district,
            payload.street,
            payload.house_number,
            payload.price_eur,
            payload.price_per_m2,
            payload.area_m2,
            payload.declared_rooms_ss,
            payload.floor,
            payload.total_floors,
            payload.building_series,
            payload.building_type,
            payload.listing_date_text,
            payload.unique_visits,
            payload.description_text,
            description_hash,
            images_count,
            raw_snapshot_hash,
            payload.raw_text_snapshot,
        ]
    )
    return tuple(values)


def _event_values(
    event_type: str,
    existing: sqlite3.Row,
    payload: ListingPayload,
) -> tuple[str | None, str | None, str | None]:
    if event_type == "price_changed":
        old_value = existing["price_eur"]
        new_value = payload.price_eur
        delta = None if old_value is None or new_value is None else new_value - old_value
        return _to_text(old_value), _to_text(new_value), _to_text(delta)
    if event_type == "unique_visits_changed":
        old_value = existing["unique_visits"]
        new_value = payload.unique_visits
        delta = None if old_value is None or new_value is None else new_value - old_value
        return _to_text(old_value), _to_text(new_value), _to_text(delta)
    if event_type == "description_changed":
        return _to_text(existing["description_hash"]), stable_hash(payload.description_text), None
    if event_type == "image_count_changed":
        return _to_text(existing["images_count"]), _to_text(len(payload.image_urls)), None
    return None, None, None


def _to_text(value: object) -> str | None:
    return None if value is None else str(value)
