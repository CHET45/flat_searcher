import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

from flat_searcher.ai import AIAnalysisPipeline, AIValidationError
from flat_searcher.db.ai_repository import ListingForAnalysis
from flat_searcher.images import ImageDownloader
from flat_searcher.services.gemini_analysis import GeminiAnalysisProvider


@dataclass(frozen=True)
class FakeBinaryResult:
    content: bytes


class FakeBinaryFetcher:
    def fetch_bytes(self, url: str) -> FakeBinaryResult:
        return FakeBinaryResult(content=b"\xff\xd8\xff" + url.encode())


class FakeModelClient:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self.responses = list(responses)
        self.image_paths: list[tuple[Path, ...]] = []

    def generate_text(self, prompt: str, image_paths: tuple[Path, ...] = ()) -> str:
        self.image_paths.append(image_paths)
        return self.responses.pop(0)


class GeminiAnalysisProviderTests(TestCase):
    def test_provider_runs_two_passes_caches_floor_plan_and_cleans_temp_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            client = FakeModelClient((_pass1_response(), _pass2_response()))
            provider = GeminiAnalysisProvider(
                pipeline=AIAnalysisPipeline(client),
                image_downloader=ImageDownloader(
                    temporary_images_dir=root / "tmp",
                    floor_plans_dir=root / "floor_plans",
                    fetcher=FakeBinaryFetcher(),
                ),
            )

            result = provider.analyze(
                ListingForAnalysis(
                    listing_id=7,
                    ss_id="gemini-provider-test",
                    ss_url="https://www.ss.com/msg/gemini-provider-test.html",
                    declared_rooms_ss=2,
                    description_text="Two-room apartment.",
                    detail_fields={"Rooms": "2"},
                    image_ids=(101, 102),
                    image_urls=(
                        "https://i.ss.com/gallery/plan.jpg",
                        "https://i.ss.com/gallery/room.jpg",
                    ),
                )
            )

            self.assertEqual(result.pass1_analysis.floor_plan_image_ids, ("101",))
            self.assertEqual(result.pass2_analysis.effective_private_rooms, 2)
            self.assertEqual(set(result.image_content_hashes), {101, 102})
            self.assertEqual(set(result.floor_plan_paths), {101})
            self.assertTrue(Path(result.floor_plan_paths[101]).exists())
            self.assertEqual(len(client.image_paths), 2)
            self.assertTrue(all(len(paths) == 2 for paths in client.image_paths))
            self.assertFalse(any((root / "tmp").iterdir()))

    def test_provider_rejects_missing_image_classification_and_cleans_temp_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            provider = GeminiAnalysisProvider(
                pipeline=AIAnalysisPipeline(
                    FakeModelClient((_incomplete_pass1_response(),))
                ),
                image_downloader=ImageDownloader(
                    temporary_images_dir=root / "tmp",
                    floor_plans_dir=root / "floor_plans",
                    fetcher=FakeBinaryFetcher(),
                ),
            )

            with self.assertRaises(AIValidationError):
                provider.analyze(
                    ListingForAnalysis(
                        listing_id=8,
                        ss_id="invalid-gemini-output",
                        ss_url="https://www.ss.com/msg/invalid-gemini-output.html",
                        declared_rooms_ss=2,
                        description_text="Two-room apartment.",
                        detail_fields={},
                        image_ids=(201, 202),
                        image_urls=(
                            "https://i.ss.com/gallery/one.jpg",
                            "https://i.ss.com/gallery/two.jpg",
                        ),
                    )
                )

            self.assertFalse(any((root / "tmp").iterdir()))


def _pass1_response() -> str:
    return """
    {
      "images": [
        {
          "image_id": "101",
          "category": "floor_plan",
          "useful_for_layout": true
        },
        {
          "image_id": "102",
          "category": "interior_room",
          "useful_for_layout": true
        }
      ],
      "floor_plan_image_ids": ["101"],
      "images_used_for_layout": ["101", "102"]
    }
    """


def _pass2_response() -> str:
    return """
    {
      "ai_detected_living_rooms": 2,
      "effective_private_rooms": 2,
      "walkthrough_rooms": 0,
      "kitchen_living_detected": false,
      "separate_kitchen_detected": true,
      "layout_class": "two_private",
      "layout_confidence_label": "Confirmed",
      "ss_vs_ai_room_conflict": false,
      "layout_explanation_user": "The floor plan supports two private rooms.",
      "floor_plan_image_ids": ["101"],
      "building_type_guess": "Panel",
      "series_guess": null,
      "wooden_building_risk": false,
      "stove_heating_risk": false,
      "mortgage_risk_level": "Low",
      "mortgage_risk_reasons": [],
      "mortgage_explanation_user": "No major mortgage risk was detected."
    }
    """


def _incomplete_pass1_response() -> str:
    return """
    {
      "images": [
        {
          "image_id": "201",
          "category": "interior_room",
          "useful_for_layout": true
        }
      ],
      "floor_plan_image_ids": []
    }
    """
