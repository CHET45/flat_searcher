from unittest import TestCase

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel, Pass2ListingAnalysis
from flat_searcher.analysis import build_listing_product_analysis


class ProductRuleTests(TestCase):
    def test_room_conflict_and_kitchen_living_flags_are_generated(self) -> None:
        product = build_listing_product_analysis(
            _analysis(
                effective_private_rooms=1,
                kitchen_living_detected=True,
                ss_vs_ai_room_conflict=True,
            ),
            declared_rooms_ss=2,
        )

        self.assertTrue(product.room_conflict)
        self.assertIn("Room conflict", product.all_flags)
        self.assertIn("AI: 1 private / SS: 2", product.all_flags)
        self.assertIn("Kitchen-living is not counted as private room", product.all_flags)

    def test_floor_plan_flag_requires_confirmed_layout(self) -> None:
        product = build_listing_product_analysis(
            _analysis(
                layout_confidence_label=LayoutConfidenceLabel.CONFIRMED,
                floor_plan_image_ids=("img-1",),
            ),
            declared_rooms_ss=2,
        )

        self.assertIn("Layout confirmed by floor plan", product.all_flags)

    def test_high_mortgage_risks_generate_flags(self) -> None:
        product = build_listing_product_analysis(
            _analysis(
                mortgage_risk_level=MortgageRiskLevel.CRITICAL,
                stove_heating_risk=True,
                wooden_building_risk=True,
            ),
            declared_rooms_ss=2,
        )

        self.assertIn("High mortgage risk", product.all_flags)
        self.assertIn("Stove heating risk", product.all_flags)
        self.assertIn("Wooden building risk", product.all_flags)


def _analysis(
    effective_private_rooms: int | None = 2,
    kitchen_living_detected: bool = False,
    ss_vs_ai_room_conflict: bool = False,
    layout_confidence_label: LayoutConfidenceLabel = LayoutConfidenceLabel.LIKELY,
    floor_plan_image_ids: tuple[str, ...] = (),
    mortgage_risk_level: MortgageRiskLevel = MortgageRiskLevel.LOW,
    stove_heating_risk: bool = False,
    wooden_building_risk: bool = False,
) -> Pass2ListingAnalysis:
    return Pass2ListingAnalysis(
        ai_detected_living_rooms=effective_private_rooms,
        effective_private_rooms=effective_private_rooms,
        walkthrough_rooms=0,
        kitchen_living_detected=kitchen_living_detected,
        separate_kitchen_detected=True,
        layout_class="test_layout",
        layout_confidence_label=layout_confidence_label,
        ss_vs_ai_room_conflict=ss_vs_ai_room_conflict,
        layout_explanation_user="Test layout explanation.",
        floor_plan_image_ids=floor_plan_image_ids,
        building_type_guess=None,
        series_guess=None,
        wooden_building_risk=wooden_building_risk,
        stove_heating_risk=stove_heating_risk,
        mortgage_risk_level=mortgage_risk_level,
        mortgage_risk_reasons=(),
        mortgage_explanation_user="Test mortgage explanation.",
    )
