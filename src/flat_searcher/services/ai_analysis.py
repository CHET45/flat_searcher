"""AI analysis execution services."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from flat_searcher.ai import (
    AIValidationError,
    ImageCategory,
    Pass1ImageAnalysis,
    Pass2ListingAnalysis,
)
from flat_searcher.ai.json_io import parse_ai_json_object
from flat_searcher.db.ai_repository import AIAnalysisRepository, ListingForAnalysis
from flat_searcher.db.repository import open_database


@dataclass(frozen=True)
class AIAnalysisRunResult:
    checked_count: int
    analyzed_count: int
    failed_count: int


@dataclass(frozen=True)
class AIProviderResult:
    pass1_raw: str
    pass1_analysis: Pass1ImageAnalysis
    pass2_raw: str
    pass2_analysis: Pass2ListingAnalysis
    image_content_hashes: dict[int, str] = field(default_factory=dict)
    floor_plan_paths: dict[int, str] = field(default_factory=dict)


class AIAnalysisProvider(Protocol):
    def analyze(
        self,
        listing: ListingForAnalysis,
    ) -> AIProviderResult: ...


class MockAIAnalysisProvider:
    def analyze(
        self,
        listing: ListingForAnalysis,
    ) -> AIProviderResult:
        pass1_payload = _mock_pass1_payload(listing)
        pass2_payload = _mock_pass2_payload(listing)
        pass1_raw = json.dumps(pass1_payload, ensure_ascii=False, sort_keys=True)
        pass2_raw = json.dumps(pass2_payload, ensure_ascii=False, sort_keys=True)
        return AIProviderResult(
            pass1_raw=pass1_raw,
            pass1_analysis=Pass1ImageAnalysis.from_dict(pass1_payload),
            pass2_raw=pass2_raw,
            pass2_analysis=Pass2ListingAnalysis.from_dict(pass2_payload),
        )


class JsonAIAnalysisProvider:
    def __init__(self, pass1_json_path: Path, pass2_json_path: Path) -> None:
        self.pass1_json_path = pass1_json_path
        self.pass2_json_path = pass2_json_path

    def analyze(
        self,
        listing: ListingForAnalysis,
    ) -> AIProviderResult:
        pass1_raw = self.pass1_json_path.read_text(encoding="utf-8")
        pass2_raw = self.pass2_json_path.read_text(encoding="utf-8")
        return AIProviderResult(
            pass1_raw=pass1_raw,
            pass1_analysis=Pass1ImageAnalysis.from_dict(parse_ai_json_object(pass1_raw)),
            pass2_raw=pass2_raw,
            pass2_analysis=Pass2ListingAnalysis.from_dict(parse_ai_json_object(pass2_raw)),
        )


class AIAnalysisService:
    def __init__(self, database_path: Path, provider: AIAnalysisProvider) -> None:
        self.database_path = database_path
        self.provider = provider

    def analyze_pending(
        self,
        analysis_version: str,
        listing_id: int | None = None,
        limit: int | None = None,
        force: bool = False,
    ) -> AIAnalysisRunResult:
        with open_database(self.database_path) as connection:
            repository = AIAnalysisRepository(connection)
            listings = repository.load_pending_listings(
                listing_id=listing_id,
                limit=limit,
                force=force,
            )
            analyzed_count = 0
            failed_count = 0
            for listing in listings:
                try:
                    analysis = self.provider.analyze(listing)
                    _validate_provider_result(listing, analysis)
                    repository.save_finished_analysis(
                        listing_id=listing.listing_id,
                        analysis_version=analysis_version,
                        analyzed_at=_now(),
                        pass1_raw_json=analysis.pass1_raw,
                        pass1_analysis=analysis.pass1_analysis,
                        pass2_raw_json=analysis.pass2_raw,
                        pass2_analysis=analysis.pass2_analysis,
                        image_content_hashes=analysis.image_content_hashes,
                        floor_plan_paths=analysis.floor_plan_paths,
                    )
                    analyzed_count += 1
                except Exception as error:
                    repository.save_failed_analysis(
                        listing_id=listing.listing_id,
                        analysis_version=analysis_version,
                        error_message=str(error),
                    )
                    failed_count += 1
            return AIAnalysisRunResult(
                checked_count=len(listings),
                analyzed_count=analyzed_count,
                failed_count=failed_count,
            )


def _mock_pass1_payload(listing: ListingForAnalysis) -> dict[str, object]:
    images = []
    for image_id in listing.image_ids:
        images.append(
            {
                "image_id": str(image_id),
                "category": ImageCategory.INTERIOR_ROOM.value,
                "useful_for_layout": True,
                "useful_for_building_type": False,
                "likely_room_group": f"room_{image_id}",
                "explanation": "Mock classification for local pipeline testing.",
            }
        )
    return {
        "images": images,
        "floor_plan_image_ids": [],
        "images_used_for_layout": [image["image_id"] for image in images],
        "images_used_for_building_type": [],
        "ignored_images": [],
        "duplicate_images": [],
        "image_groups_by_room": {
            image["likely_room_group"]: [image["image_id"]]
            for image in images
        },
    }


def _validate_provider_result(
    listing: ListingForAnalysis,
    analysis: AIProviderResult,
) -> None:
    expected = tuple(str(image_id) for image_id in listing.image_ids)
    classified = tuple(
        image.image_id for image in analysis.pass1_analysis.images
    )
    if len(classified) != len(expected) or set(classified) != set(expected):
        raise AIValidationError(
            "Pass 1 must classify every listing image exactly once."
        )
    referenced_floor_plans = (
        analysis.pass1_analysis.floor_plan_image_ids
        + analysis.pass2_analysis.floor_plan_image_ids
    )
    unknown_floor_plans = set(referenced_floor_plans) - set(expected)
    if unknown_floor_plans:
        unknown_text = ", ".join(sorted(unknown_floor_plans))
        raise AIValidationError(
            f"AI output references unknown floor plan image IDs: {unknown_text}"
        )
    unexpected_hash_ids = set(analysis.image_content_hashes) - set(listing.image_ids)
    unexpected_path_ids = set(analysis.floor_plan_paths) - set(listing.image_ids)
    if unexpected_hash_ids or unexpected_path_ids:
        raise AIValidationError(
            "AI provider returned download metadata for another listing."
        )


def _mock_pass2_payload(listing: ListingForAnalysis) -> dict[str, object]:
    declared_rooms = listing.declared_rooms_ss
    effective_rooms = declared_rooms if declared_rooms is not None and declared_rooms > 0 else None
    description = (listing.description_text or "").lower()
    stove_heating = "kr\u0101sns" in description or "stove" in description
    wooden_building = "koka" in description or "wooden" in description
    mortgage_risk = "High" if stove_heating or wooden_building else "Unknown"
    return {
        "ai_detected_living_rooms": effective_rooms,
        "effective_private_rooms": effective_rooms,
        "walkthrough_rooms": 0,
        "kitchen_living_detected": False,
        "separate_kitchen_detected": False,
        "layout_class": "mock_from_ss_rooms",
        "layout_confidence_label": "Unclear",
        "ss_vs_ai_room_conflict": False,
        "layout_explanation_user": (
            "Mock analysis: no real AI inference has been run yet. "
            "The room estimate mirrors the SS.com declared room count."
        ),
        "floor_plan_image_ids": [],
        "building_type_guess": None,
        "series_guess": None,
        "wooden_building_risk": wooden_building,
        "stove_heating_risk": stove_heating,
        "mortgage_risk_level": mortgage_risk,
        "mortgage_risk_reasons": ["Mock analysis requires real AI review."],
        "mortgage_explanation_user": (
            "Mock analysis: mortgage risk has not been evaluated by real AI yet."
        ),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
