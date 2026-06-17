from unittest import TestCase

from flat_searcher.filtering import ListingCandidate
from flat_searcher.presentation import ranking_row_view_model
from flat_searcher.ranking import RankedCandidate


class PresentationViewModelTests(TestCase):
    def test_ranking_row_view_model_formats_english_display_text(self) -> None:
        view_model = ranking_row_view_model(
            RankedCandidate(
                position=1,
                candidate=ListingCandidate(
                    listing_id=10,
                    score=82.5,
                    district="Teika",
                    street="Brivibas gatve",
                    effective_private_rooms=2,
                    declared_rooms_ss=3,
                    area_m2=58,
                    price_eur=112_000,
                    user_status="favorite",
                ),
            )
        )

        self.assertEqual(view_model.position, 1)
        self.assertEqual(view_model.score_text, "82.5")
        self.assertEqual(view_model.price_text, "112 000 EUR")
        self.assertIn("AI: 2 private / SS: 3", view_model.title)
        self.assertEqual(view_model.status_text, "favorite")
