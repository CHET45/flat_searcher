"""Two-pass Gemini provider with temporary image lifecycle management."""

from __future__ import annotations

from uuid import uuid4

from flat_searcher.ai import AIAnalysisPipeline, AIValidationError, ImagePromptInput
from flat_searcher.ai.json_io import parse_ai_json_object
from flat_searcher.db.ai_repository import ListingForAnalysis
from flat_searcher.images import DownloadedImage, ImageDownloader
from flat_searcher.services.ai_analysis import AIProviderResult


class GeminiAnalysisProvider:
    def __init__(
        self,
        pipeline: AIAnalysisPipeline,
        image_downloader: ImageDownloader,
    ) -> None:
        self.pipeline = pipeline
        self.image_downloader = image_downloader

    def analyze(self, listing: ListingForAnalysis) -> AIProviderResult:
        run_id = f"ai-{listing.listing_id}-{uuid4().hex}"
        try:
            downloaded_images = self.image_downloader.download_listing_images(
                listing_id=listing.listing_id,
                image_urls=listing.image_urls,
                run_id=run_id,
            )
            image_inputs = tuple(
                ImagePromptInput(image_id=str(image_id), source_url=image_url)
                for image_id, image_url in zip(
                    listing.image_ids,
                    listing.image_urls,
                    strict=True,
                )
            )
            image_paths = tuple(image.temporary_path for image in downloaded_images)
            pass1 = self.pipeline.analyze_images(image_inputs, image_paths)
            _validate_image_references(
                expected_image_ids=listing.image_ids,
                classified_image_ids=tuple(
                    image.image_id for image in pass1.analysis.images
                ),
                floor_plan_image_ids=pass1.analysis.floor_plan_image_ids,
            )
            pass2 = self.pipeline.analyze_listing(
                listing_text=listing.description_text or "",
                ss_fields=_ss_fields(listing),
                pass1_output=parse_ai_json_object(pass1.raw_json),
                image_paths=image_paths,
            )
            _validate_floor_plan_references(
                listing.image_ids,
                pass2.analysis.floor_plan_image_ids,
            )
            floor_plan_paths = self._cache_floor_plans(
                listing.listing_id,
                listing.image_ids,
                downloaded_images,
                pass1.analysis.floor_plan_image_ids
                + pass2.analysis.floor_plan_image_ids,
            )
            return AIProviderResult(
                pass1_raw=pass1.raw_json,
                pass1_analysis=pass1.analysis,
                pass2_raw=pass2.raw_json,
                pass2_analysis=pass2.analysis,
                image_content_hashes={
                    image_id: image.content_hash
                    for image_id, image in zip(
                        listing.image_ids,
                        downloaded_images,
                        strict=True,
                    )
                },
                floor_plan_paths=floor_plan_paths,
            )
        finally:
            self.image_downloader.cleanup_run(run_id)

    def _cache_floor_plans(
        self,
        listing_id: int,
        image_ids: tuple[int, ...],
        downloaded_images: tuple[DownloadedImage, ...],
        floor_plan_ids: tuple[str, ...],
    ) -> dict[int, str]:
        downloaded_by_id = dict(zip(image_ids, downloaded_images, strict=True))
        cached_paths: dict[int, str] = {}
        for floor_plan_id in dict.fromkeys(floor_plan_ids):
            if not floor_plan_id.isdigit():
                continue
            image_id = int(floor_plan_id)
            downloaded_image = downloaded_by_id.get(image_id)
            if downloaded_image is None:
                continue
            cached_path = self.image_downloader.cache_floor_plan(
                listing_id,
                downloaded_image,
            )
            cached_paths[image_id] = str(cached_path)
        return cached_paths


def _ss_fields(listing: ListingForAnalysis) -> dict[str, object]:
    return {
        "ss_id": listing.ss_id,
        "ss_url": listing.ss_url,
        "declared_rooms_ss": listing.declared_rooms_ss,
        "detail_fields": listing.detail_fields,
    }


def _validate_image_references(
    expected_image_ids: tuple[int, ...],
    classified_image_ids: tuple[str, ...],
    floor_plan_image_ids: tuple[str, ...],
) -> None:
    expected = tuple(str(image_id) for image_id in expected_image_ids)
    if len(classified_image_ids) != len(expected) or set(classified_image_ids) != set(expected):
        raise AIValidationError(
            "Pass 1 must classify every listing image exactly once."
        )
    _validate_floor_plan_references(expected_image_ids, floor_plan_image_ids)


def _validate_floor_plan_references(
    expected_image_ids: tuple[int, ...],
    floor_plan_image_ids: tuple[str, ...],
) -> None:
    expected = {str(image_id) for image_id in expected_image_ids}
    unknown_ids = set(floor_plan_image_ids) - expected
    if unknown_ids:
        unknown_text = ", ".join(sorted(unknown_ids))
        raise AIValidationError(
            f"AI output references unknown floor plan image IDs: {unknown_text}"
        )
