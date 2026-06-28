"""End-to-end processing of pending listing analysis and persisted scores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from flat_searcher.db.ai_repository import ListingForAnalysis
from flat_searcher.services.ai_analysis import (
    AIAnalysisProvider,
    AIAnalysisRunResult,
    AIAnalysisService,
)
from flat_searcher.services.location_scoring import (
    LocationScoreRunResult,
    LocationScoreService,
)
from flat_searcher.services.scoring import (
    ScoreRecalculationResult,
    ScoreRecalculationService,
)


@dataclass(frozen=True)
class ListingProcessingResult:
    ai: AIAnalysisRunResult
    location: LocationScoreRunResult
    scoring: ScoreRecalculationResult


class ListingProcessingService:
    def __init__(
        self,
        database_path: Path,
        analysis_provider: AIAnalysisProvider,
    ) -> None:
        self.database_path = database_path
        self.analysis_provider = analysis_provider

    def process(
        self,
        analysis_version: str,
        profile_key: str = "for_living_mortgage",
        listing_id: int | None = None,
        limit: int | None = None,
        force_analysis: bool = False,
        progress_callback: Callable[[ListingForAnalysis, int, int], None] | None = None,
        analysis_order: tuple[int, ...] | None = None,
        force_listing_ids: frozenset[int] = frozenset(),
    ) -> ListingProcessingResult:
        ai_service = AIAnalysisService(
            database_path=self.database_path,
            provider=self.analysis_provider,
        )
        if listing_id is None and analysis_order is not None:
            forced_ids = frozenset(analysis_order) if force_analysis else force_listing_ids
            ai_result = ai_service.analyze_ordered(
                analysis_version=analysis_version,
                listing_ids=analysis_order,
                force_listing_ids=forced_ids,
                progress_callback=progress_callback,
            )
        else:
            ai_result = ai_service.analyze_pending(
                analysis_version=analysis_version,
                listing_id=listing_id,
                limit=limit,
                force=force_analysis,
                progress_callback=progress_callback,
            )
        location_result = LocationScoreService(self.database_path).recalculate()
        scoring_result = ScoreRecalculationService(self.database_path).recalculate(profile_key)
        return ListingProcessingResult(
            ai=ai_result,
            location=location_result,
            scoring=scoring_result,
        )
