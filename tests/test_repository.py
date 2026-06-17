import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.repository import ListingRepository, open_database
from flat_searcher.scraper import SSDetailParser, SSListParser, merge_listing


FIXTURES = Path(__file__).parent / "fixtures" / "ss"
LIST_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"
DETAIL_URL = "https://www.ss.com/msg/lv/real-estate/flats/riga/centre/bhodf.html"


class ListingRepositoryTests(TestCase):
    def test_upsert_creates_listing_snapshot_images_and_change_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)
            payload = _fixture_payload()

            with open_database(database_path) as connection:
                repository = ListingRepository(connection)
                first_run_id = repository.create_app_run("test", "2026-06-17T10:00:00+00:00")
                first_result = repository.upsert_listing(
                    payload,
                    app_run_id=first_run_id,
                    checked_at="2026-06-17T10:00:01+00:00",
                )

                changed_payload = replace(payload, price_eur=(payload.price_eur or 0) - 1000)
                second_run_id = repository.create_app_run("test", "2026-06-17T11:00:00+00:00")
                second_result = repository.upsert_listing(
                    changed_payload,
                    app_run_id=second_run_id,
                    checked_at="2026-06-17T11:00:01+00:00",
                )

                self.assertTrue(first_result.is_new)
                self.assertFalse(second_result.is_new)
                self.assertIn("price_changed", second_result.change_events)
                self.assertEqual(repository.count_listings(), 1)

                snapshot_count = connection.execute(
                    "SELECT COUNT(*) FROM listing_snapshots"
                ).fetchone()[0]
                image_count = connection.execute("SELECT COUNT(*) FROM listing_images").fetchone()[0]
                price_events = connection.execute(
                    """
                    SELECT COUNT(*) FROM listing_change_events
                    WHERE event_type = 'price_changed'
                    """
                ).fetchone()[0]

                self.assertEqual(snapshot_count, 2)
                self.assertGreater(image_count, 10)
                self.assertEqual(price_events, 1)


def _fixture_payload():
    list_html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")
    summary = SSListParser().parse(list_html, LIST_URL)[0]
    detail = SSDetailParser().parse(detail_html, DETAIL_URL)
    return merge_listing(summary, detail)
