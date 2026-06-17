from unittest import TestCase

from flat_searcher.filtering import ListingCandidate, ListingFilters
from flat_searcher.ranking import rank_candidates


class RankingTests(TestCase):
    def test_ranking_uses_filtered_visible_set_and_positions(self) -> None:
        candidates = (
            ListingCandidate(listing_id=1, score=80, price_eur=100_000),
            ListingCandidate(listing_id=2, score=95, price_eur=120_000, is_rejected=True),
            ListingCandidate(listing_id=3, score=90, price_eur=130_000),
        )

        ranked = rank_candidates(candidates, ListingFilters())

        self.assertEqual([item.position for item in ranked], [1, 2])
        self.assertEqual([item.candidate.listing_id for item in ranked], [3, 1])

    def test_unknown_scores_sort_after_known_scores(self) -> None:
        candidates = (
            ListingCandidate(listing_id=1, score=None),
            ListingCandidate(listing_id=2, score=10),
        )

        ranked = rank_candidates(candidates, ListingFilters())

        self.assertEqual([item.candidate.listing_id for item in ranked], [2, 1])
