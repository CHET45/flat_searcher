import json
import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingReadRepository, ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.models import ListingPayload
from flat_searcher.services.ai_analysis import AIAnalysisService, MockAIAnalysisProvider
from flat_searcher.services.scoring import ScoreRecalculationService


class ScoreRecalculationServiceTests(TestCase):
    def test_recalculate_persists_profile_price_value_and_overall_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                first_id = _insert_listing(connection, "score-1", 90_000, 50)
                _insert_listing(connection, "score-2", 120_000, 55)
                _insert_listing(connection, "score-3", 150_000, 60)

            AIAnalysisService(
                database_path=database_path,
                provider=MockAIAnalysisProvider(),
            ).analyze_pending(analysis_version="mock-scoring-test")

            first_result = ScoreRecalculationService(database_path).recalculate()
            second_result = ScoreRecalculationService(database_path).recalculate()

            with open_database(database_path) as connection:
                profile = connection.execute(
                    """
                    SELECT profile_name, enabled_blocks_json, block_weights_json
                    FROM scoring_profiles
                    WHERE profile_key = 'for_living_mortgage'
                    """
                ).fetchone()
                price_rows = connection.execute(
                    "SELECT * FROM price_value_analyses ORDER BY listing_id"
                ).fetchall()
                score_rows = connection.execute(
                    "SELECT * FROM score_results ORDER BY listing_id"
                ).fetchall()
                candidates = ListingReadRepository(connection).load_candidates(
                    "for_living_mortgage"
                )

            self.assertEqual(first_result.listing_count, 3)
            self.assertEqual(first_result.scored_count, 3)
            self.assertEqual(second_result, first_result)
            self.assertEqual(profile["profile_name"], "For living + mortgage")
            self.assertIn("price_value", json.loads(profile["enabled_blocks_json"]))
            self.assertEqual(
                json.loads(profile["block_weights_json"])["price_value"],
                "Critical factor",
            )
            self.assertEqual(len(price_rows), 3)
            self.assertEqual(len(score_rows), 3)
            self.assertTrue(all(row["price_value_score"] is not None for row in price_rows))
            self.assertTrue(all(row["overall_score"] is not None for row in score_rows))
            first_candidate = next(
                candidate for candidate in candidates if candidate.listing_id == first_id
            )
            self.assertIsNotNone(first_candidate.score)


def _insert_listing(connection, ss_id: str, price_eur: int, area_m2: float) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id=ss_id,
            ss_url=f"https://www.ss.com/msg/{ss_id}.html",
            district="Teika",
            street="Brivibas iela",
            house_number="100",
            price_eur=price_eur,
            area_m2=area_m2,
            declared_rooms_ss=2,
            description_text="Apartment listing text.",
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    return result.listing_id
