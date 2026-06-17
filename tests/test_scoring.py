from unittest import TestCase

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.scoring import (
    BlockScore,
    ScoreBlockKey,
    calculate_weighted_score,
    default_living_mortgage_profile,
    layout_confidence_score,
    mortgage_suitability_score,
    room_privacy_score,
)


class ScoringTests(TestCase):
    def test_default_profile_does_not_include_views_block(self) -> None:
        profile = default_living_mortgage_profile()

        self.assertNotIn("views", {block.value for block in profile.block_importance})

    def test_basic_block_scores_follow_product_direction(self) -> None:
        self.assertEqual(room_privacy_score(2), 100.0)
        self.assertLess(room_privacy_score(1), room_privacy_score(2))
        self.assertGreater(
            layout_confidence_score(LayoutConfidenceLabel.CONFIRMED),
            layout_confidence_score(LayoutConfidenceLabel.UNCLEAR),
        )
        self.assertGreater(
            mortgage_suitability_score(MortgageRiskLevel.LOW),
            mortgage_suitability_score(MortgageRiskLevel.HIGH),
        )

    def test_weighted_score_uses_enabled_profile_blocks(self) -> None:
        profile = default_living_mortgage_profile()

        result = calculate_weighted_score(
            profile,
            (
                BlockScore(ScoreBlockKey.PRICE_VALUE, 80),
                BlockScore(ScoreBlockKey.ROOM_PRIVACY, 100),
                BlockScore(ScoreBlockKey.FLOOR, 0),
            ),
        )

        self.assertIsNotNone(result.overall_score)
        self.assertGreater(result.overall_score or 0, 80)
