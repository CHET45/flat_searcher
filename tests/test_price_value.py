from unittest import TestCase

from flat_searcher.ai import MortgageRiskLevel
from flat_searcher.scoring import (
    MarketBaselineLevel,
    MarketListing,
    calculate_price_value,
    choose_market_baseline,
    is_suspiciously_low_price,
    relative_market_score,
)


class PriceValueTests(TestCase):
    def test_baseline_prefers_specific_comparables_when_sample_is_large_enough(self) -> None:
        target = MarketListing(
            listing_id=1,
            price_eur=90_000,
            area_m2=50,
            district="Teika",
            declared_rooms_ss=2,
            effective_private_rooms=2,
            building_series="602.",
        )
        listings = tuple(
            MarketListing(
                listing_id=index + 2,
                price_eur=100_000 + index * 1_000,
                area_m2=50,
                district="Teika",
                declared_rooms_ss=2,
                effective_private_rooms=2,
                building_series="602.",
            )
            for index in range(6)
        )

        baseline = choose_market_baseline(target, listings, minimum_sample_size=5)

        self.assertIsNotNone(baseline)
        self.assertEqual(baseline.level, MarketBaselineLevel.SERIES_BUILDING)

    def test_critical_risk_and_inactive_listings_are_excluded_from_baseline(self) -> None:
        target = MarketListing(listing_id=1, price_eur=90_000, area_m2=50)
        listings = (
            MarketListing(
                listing_id=2,
                price_eur=10_000,
                area_m2=50,
                mortgage_risk_level=MortgageRiskLevel.CRITICAL,
            ),
            MarketListing(listing_id=3, price_eur=100_000, area_m2=50, is_active=False),
            MarketListing(listing_id=4, price_eur=110_000, area_m2=50),
            MarketListing(listing_id=5, price_eur=120_000, area_m2=50),
        )

        baseline = choose_market_baseline(target, listings, minimum_sample_size=10)

        self.assertIsNotNone(baseline)
        self.assertEqual(baseline.sample_size, 2)

    def test_relative_market_score_rewards_better_value(self) -> None:
        self.assertGreater(relative_market_score(1_600, 2_000), relative_market_score(2_400, 2_000))

    def test_suspicious_low_price_flag_uses_baseline_context(self) -> None:
        target = MarketListing(listing_id=1, price_eur=50_000, area_m2=50)
        listings = tuple(
            MarketListing(listing_id=index + 2, price_eur=100_000, area_m2=50)
            for index in range(6)
        )

        result = calculate_price_value(target, listings)

        self.assertTrue(result.suspicious_low_price_flag)
        self.assertTrue(is_suspiciously_low_price(target.price_per_m2 or 0, result.baseline))
