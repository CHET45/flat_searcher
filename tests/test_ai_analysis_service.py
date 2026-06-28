import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingReadRepository, ListingRepository, open_database
from flat_searcher.db.ai_repository import ListingForAnalysis
from flat_searcher.db.bootstrap import init_database
from flat_searcher.models import ListingPayload
from flat_searcher.services.ai_analysis import (
    AIAnalysisService,
    AIProviderResult,
    MockAIAnalysisProvider,
)


class InvalidImageReferenceProvider:
    def analyze(self, listing: ListingForAnalysis) -> AIProviderResult:
        result = MockAIAnalysisProvider().analyze(listing)
        invalid_image = replace(
            result.pass1_analysis.images[0],
            image_id="999999",
        )
        return replace(
            result,
            pass1_analysis=replace(
                result.pass1_analysis,
                images=(invalid_image,),
            ),
        )


class RecordingProvider:
    def __init__(self) -> None:
        self.seen_ids: list[int] = []

    def analyze(self, listing: ListingForAnalysis) -> AIProviderResult:
        self.seen_ids.append(listing.listing_id)
        return MockAIAnalysisProvider().analyze(listing)


class AIAnalysisServiceTests(TestCase):
    def test_mock_analysis_is_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _insert_listing(connection)

            progress_events = []
            result = AIAnalysisService(
                database_path=database_path,
                provider=MockAIAnalysisProvider(),
            ).analyze_pending(
                analysis_version="mock-test-v1",
                progress_callback=lambda listing, current, total: progress_events.append(
                    (listing.listing_id, listing.district, current, total)
                ),
            )

            with open_database(database_path) as connection:
                detail = ListingReadRepository(connection).load_detail(
                    listing_id,
                    "for_living_mortgage",
                )
                analysis_row = connection.execute(
                    """
                    SELECT status, analysis_version, effective_private_rooms,
                           layout_confidence_label, pass1_output_json, pass2_output_json
                    FROM ai_analyses
                    WHERE listing_id = ?
                    """,
                    (listing_id,),
                ).fetchone()
                image_category = connection.execute(
                    "SELECT image_category FROM listing_images WHERE listing_id = ?",
                    (listing_id,),
                ).fetchone()[0]
                needs_ai_analysis = connection.execute(
                    "SELECT needs_ai_analysis FROM listings WHERE id = ?",
                    (listing_id,),
                ).fetchone()[0]

            self.assertEqual(result.checked_count, 1)
            self.assertEqual(result.analyzed_count, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(progress_events, [(listing_id, "Teika", 1, 1)])
            self.assertEqual(analysis_row["status"], "finished")
            self.assertEqual(analysis_row["analysis_version"], "mock-test-v1")
            self.assertEqual(analysis_row["effective_private_rooms"], 2)
            self.assertEqual(analysis_row["layout_confidence_label"], "Unclear")
            self.assertIsNotNone(analysis_row["pass1_output_json"])
            self.assertIsNotNone(analysis_row["pass2_output_json"])
            self.assertEqual(image_category, "interior_room")
            self.assertEqual(needs_ai_analysis, 0)
            self.assertIsNotNone(detail)
            self.assertEqual(detail.effective_private_rooms, 2)

    def test_invalid_image_references_are_saved_as_failed_without_cross_listing_updates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _insert_listing(connection)

            result = AIAnalysisService(
                database_path=database_path,
                provider=InvalidImageReferenceProvider(),
            ).analyze_pending(analysis_version="invalid-image-test")

            with open_database(database_path) as connection:
                analysis_row = connection.execute(
                    """
                    SELECT status, error_message
                    FROM ai_analyses
                    WHERE listing_id = ?
                    """,
                    (listing_id,),
                ).fetchone()
                image_category = connection.execute(
                    "SELECT image_category FROM listing_images WHERE listing_id = ?",
                    (listing_id,),
                ).fetchone()[0]
                needs_ai_analysis = connection.execute(
                    "SELECT needs_ai_analysis FROM listings WHERE id = ?",
                    (listing_id,),
                ).fetchone()[0]

            self.assertEqual(result.failed_count, 1)
            self.assertEqual(analysis_row["status"], "failed")
            self.assertIn("classify every listing image", analysis_row["error_message"])
            self.assertIsNone(image_category)
            self.assertEqual(needs_ai_analysis, 1)

    def test_force_creates_a_new_finished_analysis_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _insert_listing(connection)

            service = AIAnalysisService(
                database_path=database_path,
                provider=MockAIAnalysisProvider(),
            )
            service.analyze_pending(analysis_version="first")
            result = service.analyze_pending(
                analysis_version="second",
                listing_id=listing_id,
                force=True,
            )

            with open_database(database_path) as connection:
                versions = connection.execute(
                    """
                    SELECT analysis_version
                    FROM ai_analyses
                    WHERE listing_id = ? AND status = 'finished'
                    ORDER BY id
                    """,
                    (listing_id,),
                ).fetchall()

            self.assertEqual(result.analyzed_count, 1)
            self.assertEqual(
                [row["analysis_version"] for row in versions],
                ["first", "second"],
            )

    def test_ordered_analysis_uses_requested_listing_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                first_id = _insert_listing(connection, ss_id="test-ai-1")
                second_id = _insert_listing(connection, ss_id="test-ai-2")

            provider = RecordingProvider()
            progress_events = []
            result = AIAnalysisService(
                database_path=database_path,
                provider=provider,
            ).analyze_ordered(
                analysis_version="ordered-test",
                listing_ids=(second_id, first_id),
                progress_callback=lambda listing, current, total: progress_events.append(
                    (listing.listing_id, current, total)
                ),
            )

            self.assertEqual(result.analyzed_count, 2)
            self.assertEqual(provider.seen_ids, [second_id, first_id])
            self.assertEqual(
                progress_events,
                [(second_id, 1, 2), (first_id, 2, 2)],
            )

    def test_ordered_analysis_can_force_already_analyzed_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _insert_listing(connection)

            service = AIAnalysisService(
                database_path=database_path,
                provider=MockAIAnalysisProvider(),
            )
            service.analyze_pending(analysis_version="first")
            result = service.analyze_ordered(
                analysis_version="forced",
                listing_ids=(listing_id,),
                force_listing_ids=frozenset({listing_id}),
            )

            with open_database(database_path) as connection:
                versions = connection.execute(
                    """
                    SELECT analysis_version
                    FROM ai_analyses
                    WHERE listing_id = ? AND status = 'finished'
                    ORDER BY id
                    """,
                    (listing_id,),
                ).fetchall()

            self.assertEqual(result.analyzed_count, 1)
            self.assertEqual([row["analysis_version"] for row in versions], ["first", "forced"])


def _insert_listing(connection, ss_id: str = "test-ai-1") -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id=ss_id,
            ss_url=f"https://www.ss.com/msg/{ss_id}.html",
            district="Teika",
            street="Brivibas",
            house_number="100",
            price_eur=100_000,
            area_m2=50,
            declared_rooms_ss=2,
            description_text="Apartment listing text.",
            image_urls=("https://i.ss.com/gallery/test-ai-1.jpg",),
            raw_html="<html>test</html>",
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    return result.listing_id

class AIAnalysisCancellationTests(TestCase):
    def test_analyze_pending_can_stop_before_next_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_database(db_path)
            with open_database(db_path) as connection:
                _insert_listing(connection, "cancel-1")
                _insert_listing(connection, "cancel-2")
                connection.commit()

            calls = 0
            def should_cancel() -> bool:
                return calls >= 1

            class CountingProvider(MockAIAnalysisProvider):
                def analyze(self, listing):
                    nonlocal calls
                    calls += 1
                    return super().analyze(listing)

            result = AIAnalysisService(db_path, CountingProvider()).analyze_pending(
                analysis_version="test",
                progress_callback=None,
                should_cancel=should_cancel,
            )

            self.assertTrue(result.cancelled)
            self.assertEqual(result.checked_count, 1)
            self.assertEqual(result.analyzed_count, 1)
