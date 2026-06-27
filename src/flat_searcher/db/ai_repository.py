"""Persistence for AI analysis runs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from flat_searcher.ai import Pass1ImageAnalysis, Pass2ListingAnalysis


@dataclass(frozen=True)
class ListingForAnalysis:
    listing_id: int
    ss_id: str
    ss_url: str
    declared_rooms_ss: int | None
    description_text: str | None
    detail_fields: dict[str, object]
    image_ids: tuple[int, ...]
    image_urls: tuple[str, ...]


class AIAnalysisRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def load_pending_listings(
        self,
        listing_id: int | None = None,
        limit: int | None = None,
        force: bool = False,
    ) -> tuple[ListingForAnalysis, ...]:
        query = """
            SELECT l.id, l.ss_id, l.ss_url, l.declared_rooms_ss, l.description_text,
                   l.detail_fields_json
            FROM listings l
            WHERE l.listing_status = 'active'
              AND l.description_text IS NOT NULL
              AND (? IS NULL OR l.id = ?)
              AND (
                  ? = 1
                  OR l.needs_ai_analysis = 1
                  OR NOT EXISTS (
                      SELECT 1 FROM ai_analyses a
                      WHERE a.listing_id = l.id AND a.status = 'finished'
                  )
              )
            ORDER BY l.id
        """
        params: list[object] = [listing_id, listing_id, 1 if force else 0]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return tuple(self._analysis_record_from_row(row) for row in rows)

    def save_finished_analysis(
        self,
        listing_id: int,
        analysis_version: str,
        analyzed_at: str,
        pass1_raw_json: str,
        pass1_analysis: Pass1ImageAnalysis,
        pass2_raw_json: str,
        pass2_analysis: Pass2ListingAnalysis,
        image_content_hashes: dict[int, str] | None = None,
        floor_plan_paths: dict[int, str] | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO ai_analyses (
                listing_id, analysis_version, status, analyzed_at,
                ai_detected_living_rooms, effective_private_rooms, walkthrough_rooms,
                kitchen_living_detected, separate_kitchen_detected, layout_class,
                layout_confidence_label, ss_vs_ai_room_conflict, layout_explanation_user,
                floor_plan_image_ids, building_type_guess, series_guess,
                wooden_building_risk, stove_heating_risk, mortgage_risk_level,
                mortgage_risk_reasons, mortgage_explanation_user,
                pass1_output_json, pass2_output_json
            )
            VALUES (
                ?, ?, 'finished', ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                listing_id,
                analysis_version,
                analyzed_at,
                pass2_analysis.ai_detected_living_rooms,
                pass2_analysis.effective_private_rooms,
                pass2_analysis.walkthrough_rooms,
                1 if pass2_analysis.kitchen_living_detected else 0,
                1 if pass2_analysis.separate_kitchen_detected else 0,
                pass2_analysis.layout_class,
                pass2_analysis.layout_confidence_label.value,
                1 if pass2_analysis.ss_vs_ai_room_conflict else 0,
                pass2_analysis.layout_explanation_user,
                json.dumps(pass2_analysis.floor_plan_image_ids, ensure_ascii=False),
                pass2_analysis.building_type_guess,
                pass2_analysis.series_guess,
                1 if pass2_analysis.wooden_building_risk else 0,
                1 if pass2_analysis.stove_heating_risk else 0,
                pass2_analysis.mortgage_risk_level.value,
                json.dumps(pass2_analysis.mortgage_risk_reasons, ensure_ascii=False),
                pass2_analysis.mortgage_explanation_user,
                pass1_raw_json,
                pass2_raw_json,
            ),
        )
        self._apply_pass1_image_metadata(listing_id, pass1_analysis)
        self._apply_download_metadata(
            listing_id,
            image_content_hashes or {},
            floor_plan_paths or {},
        )
        self.connection.execute(
            """
            UPDATE listings
            SET needs_ai_analysis = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (listing_id,),
        )
        return int(cursor.lastrowid)

    def save_failed_analysis(
        self,
        listing_id: int,
        analysis_version: str,
        error_message: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO ai_analyses (listing_id, analysis_version, status, error_message)
            VALUES (?, ?, 'failed', ?)
            """,
            (listing_id, analysis_version, error_message),
        )
        return int(cursor.lastrowid)

    def _analysis_record_from_row(self, row: sqlite3.Row) -> ListingForAnalysis:
        image_rows = self.connection.execute(
            """
            SELECT id, source_url
            FROM listing_images
            WHERE listing_id = ?
            ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
        return ListingForAnalysis(
            listing_id=row["id"],
            ss_id=row["ss_id"],
            ss_url=row["ss_url"],
            declared_rooms_ss=row["declared_rooms_ss"],
            description_text=row["description_text"],
            detail_fields=json.loads(row["detail_fields_json"] or "{}"),
            image_ids=tuple(image_row["id"] for image_row in image_rows),
            image_urls=tuple(image_row["source_url"] for image_row in image_rows),
        )

    def _apply_pass1_image_metadata(
        self,
        listing_id: int,
        pass1_analysis: Pass1ImageAnalysis,
    ) -> None:
        for classified_image in pass1_analysis.images:
            if not classified_image.image_id.isdigit():
                continue
            self.connection.execute(
                """
                UPDATE listing_images
                SET image_category = ?,
                    is_floor_plan = CASE WHEN ? = 'floor_plan' THEN 1 ELSE is_floor_plan END
                WHERE id = ? AND listing_id = ?
                """,
                (
                    classified_image.category.value,
                    classified_image.category.value,
                    int(classified_image.image_id),
                    listing_id,
                ),
            )

    def _apply_download_metadata(
        self,
        listing_id: int,
        image_content_hashes: dict[int, str],
        floor_plan_paths: dict[int, str],
    ) -> None:
        for image_id, content_hash in image_content_hashes.items():
            self.connection.execute(
                """
                UPDATE listing_images
                SET content_hash = ?
                WHERE id = ? AND listing_id = ?
                """,
                (content_hash, image_id, listing_id),
            )
        for image_id, floor_plan_path in floor_plan_paths.items():
            self.connection.execute(
                """
                UPDATE listing_images
                SET is_floor_plan = 1, local_floor_plan_path = ?
                WHERE id = ? AND listing_id = ?
                """,
                (floor_plan_path, image_id, listing_id),
            )
