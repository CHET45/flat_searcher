from unittest import TestCase

from flat_searcher.ai import (
    AIValidationError,
    ImageCategory,
    LayoutConfidenceLabel,
    MortgageRiskLevel,
    Pass1ImageAnalysis,
    Pass2ListingAnalysis,
)
from flat_searcher.ai.json_io import parse_ai_json_object
from flat_searcher.ai.prompts import ImagePromptInput, build_pass1_prompt, build_pass2_prompt


class AISchemaTests(TestCase):
    def test_pass1_schema_accepts_valid_image_classification(self) -> None:
        analysis = Pass1ImageAnalysis.from_dict(
            {
                "images": [
                    {
                        "image_id": "img-1",
                        "category": "floor_plan",
                        "useful_for_layout": True,
                    }
                ],
                "floor_plan_image_ids": ["img-1"],
                "image_groups_by_room": {"plan": ["img-1"]},
            }
        )

        self.assertEqual(analysis.images[0].category, ImageCategory.FLOOR_PLAN)
        self.assertEqual(analysis.floor_plan_image_ids, ("img-1",))

    def test_pass1_schema_rejects_unknown_category(self) -> None:
        with self.assertRaises(AIValidationError):
            Pass1ImageAnalysis.from_dict(
                {"images": [{"image_id": "img-1", "category": "unknown"}]}
            )

    def test_pass2_schema_accepts_valid_layout_and_mortgage_analysis(self) -> None:
        analysis = Pass2ListingAnalysis.from_dict(
            {
                "ai_detected_living_rooms": 3,
                "effective_private_rooms": 2,
                "walkthrough_rooms": 1,
                "kitchen_living_detected": False,
                "separate_kitchen_detected": True,
                "layout_class": "two_private_plus_walkthrough",
                "layout_confidence_label": "Likely",
                "ss_vs_ai_room_conflict": True,
                "layout_explanation_user": "AI sees two private rooms and one walkthrough room.",
                "floor_plan_image_ids": [],
                "building_type_guess": "Panel",
                "series_guess": "602.",
                "wooden_building_risk": False,
                "stove_heating_risk": False,
                "mortgage_risk_level": "Medium",
                "mortgage_risk_reasons": ["Needs manual building condition check."],
                "mortgage_explanation_user": "No critical mortgage risk was detected.",
            }
        )

        self.assertEqual(analysis.layout_confidence_label, LayoutConfidenceLabel.LIKELY)
        self.assertEqual(analysis.mortgage_risk_level, MortgageRiskLevel.MEDIUM)

    def test_json_parser_requires_object(self) -> None:
        with self.assertRaises(AIValidationError):
            parse_ai_json_object("[]")

    def test_prompt_builders_include_required_rules(self) -> None:
        pass1_prompt = build_pass1_prompt(
            (ImagePromptInput(image_id="img-1", source_url="https://example.test/1.jpg"),)
        )
        pass2_prompt = build_pass2_prompt(
            listing_text="Listing text",
            ss_fields={"Istabas": 3},
            pass1_output={"images": []},
        )

        self.assertIn("Do not aggressively discard interior photos", pass1_prompt)
        self.assertIn("Kitchen-living is not a private room", pass2_prompt)
