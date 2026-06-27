"""End-to-end processing of pending listing analysis and persisted scores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    ) -> ListingProcessingResult:
        ai_result = AIAnalysisService(
            database_path=self.database_path,
            provider=self.analysis_provider,
        ).analyze_pending(
            analysis_version=analysis_version,
            listing_id=listing_id,
            limit=limit,
            force=force_analysis,
        )
        location_result = LocationScoreService(self.database_path).recalculate()
        scoring_result = ScoreRecalculationService(self.database_path).recalculate(
            profile_key
        )
        return ListingProcessingResult(
            ai=ai_result,
            location=location_result,
            scoring=scoring_result,
        )
