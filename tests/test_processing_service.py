import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.models import ListingPayload
from flat_searcher.services.ai_analysis import MockAIAnalysisProvider
from flat_searcher.services.processing import ListingProcessingService


class ListingProcessingServiceTests(TestCase):
    def test_process_runs_pending_analysis_location_and_overall_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = ListingRepository(connection)
                run_id = repository.create_app_run(
                    "test",
                    "2026-06-17T12:00:00+00:00",
                )
                repository.upsert_listing(
                    ListingPayload(
                        ss_id="processing-test",
                        ss_url="https://www.ss.com/msg/processing-test.html",
                        district="Teika",
                        price_eur=100_000,
                        area_m2=50,
                        declared_rooms_ss=2,
                        description_text="Apartment listing text.",
                    ),
                    app_run_id=run_id,
                    checked_at="2026-06-17T12:00:01+00:00",
                )

            result = ListingProcessingService(
                database_path=database_path,
                analysis_provider=MockAIAnalysisProvider(),
            ).process(analysis_version="processing-test-v1")

            with open_database(database_path) as connection:
                score_count = connection.execute(
                    "SELECT COUNT(*) FROM score_results"
                ).fetchone()[0]

            self.assertEqual(result.ai.analyzed_count, 1)
            self.assertEqual(result.location.listing_count, 0)
            self.assertEqual(result.scoring.scored_count, 1)
            self.assertEqual(score_count, 1)
