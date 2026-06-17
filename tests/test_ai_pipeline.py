from pathlib import Path
from unittest import TestCase

from flat_searcher.ai import AIAnalysisPipeline, ImagePromptInput, LayoutConfidenceLabel


class FakeModelClient:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.image_paths: list[tuple[Path, ...]] = []

    def generate_text(self, prompt: str, image_paths: tuple[Path, ...] = ()) -> str:
        self.prompts.append(prompt)
        self.image_paths.append(image_paths)
        return self.responses.pop(0)


class AIPipelineTests(TestCase):
    def test_analyze_images_validates_pass1_response(self) -> None:
        client = FakeModelClient(
            (
                """
                {
                  "images": [
                    {
                      "image_id": "img-1",
                      "category": "floor_plan",
                      "useful_for_layout": true
                    }
                  ],
                  "floor_plan_image_ids": ["img-1"]
                }
                """,
            )
        )
        pipeline = AIAnalysisPipeline(client)

        result = pipeline.analyze_images(
            images=(ImagePromptInput("img-1", "https://example.test/1.jpg"),),
            image_paths=(Path("1.jpg"),),
        )

        self.assertEqual(result.analysis.floor_plan_image_ids, ("img-1",))
        self.assertIn("Classify every image", client.prompts[0])
        self.assertEqual(client.image_paths[0], (Path("1.jpg"),))

    def test_analyze_listing_validates_pass2_response(self) -> None:
        client = FakeModelClient(
            (
                """
                {
                  "ai_detected_living_rooms": 2,
                  "effective_private_rooms": 2,
                  "walkthrough_rooms": 0,
                  "kitchen_living_detected": false,
                  "separate_kitchen_detected": true,
                  "layout_class": "two_private",
                  "layout_confidence_label": "Confirmed",
                  "ss_vs_ai_room_conflict": false,
                  "layout_explanation_user": "The floor plan confirms two private rooms.",
                  "floor_plan_image_ids": ["img-1"],
                  "building_type_guess": "Panel",
                  "series_guess": "602.",
                  "wooden_building_risk": false,
                  "stove_heating_risk": false,
                  "mortgage_risk_level": "Low",
                  "mortgage_risk_reasons": [],
                  "mortgage_explanation_user": "No major mortgage risk detected."
                }
                """,
            )
        )
        pipeline = AIAnalysisPipeline(client)

        result = pipeline.analyze_listing(
            listing_text="Listing text",
            ss_fields={"rooms": 2},
            pass1_output={"images": []},
            image_paths=(Path("1.jpg"),),
        )

        self.assertEqual(result.analysis.layout_confidence_label, LayoutConfidenceLabel.CONFIRMED)
        self.assertIn("Kitchen-living is not a private room", client.prompts[0])
