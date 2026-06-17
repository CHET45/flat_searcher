from unittest import TestCase

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.filtering import ListingCandidate, ListingFilters, filter_candidates


class FilteringTests(TestCase):
    def test_default_filters_hide_rejected_and_inactive_candidates(self) -> None:
        candidates = (
            ListingCandidate(listing_id=1, score=80),
            ListingCandidate(listing_id=2, score=90, is_rejected=True),
            ListingCandidate(listing_id=3, score=70, listing_status="inactive"),
        )

        visible = filter_candidates(candidates, ListingFilters())

        self.assertEqual(tuple(candidate.listing_id for candidate in visible), (1,))

    def test_filters_do_not_change_scores(self) -> None:
        candidate = ListingCandidate(listing_id=1, score=88, district="Teika", price_eur=100_000)

        visible = filter_candidates(
            (candidate,),
            ListingFilters(districts=frozenset({"Teika"}), price_max=120_000),
        )

        self.assertEqual(visible[0].score, 88)

    def test_layout_and_mortgage_filters(self) -> None:
        candidates = (
            ListingCandidate(
                listing_id=1,
                score=80,
                layout_confidence_label=LayoutConfidenceLabel.CONFIRMED,
                mortgage_risk_level=MortgageRiskLevel.LOW,
                has_floor_plan=True,
            ),
            ListingCandidate(
                listing_id=2,
                score=70,
                layout_confidence_label=LayoutConfidenceLabel.UNCLEAR,
                mortgage_risk_level=MortgageRiskLevel.HIGH,
                has_floor_plan=False,
            ),
        )

        visible = filter_candidates(
            candidates,
            ListingFilters(
                only_confirmed_layout=True,
                hide_high_mortgage_risk=True,
                only_with_floor_plan=True,
            ),
        )

        self.assertEqual(tuple(candidate.listing_id for candidate in visible), (1,))
