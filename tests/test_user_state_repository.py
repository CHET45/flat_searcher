import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.repository import ListingRepository, open_database
from flat_searcher.db.user_state_repository import UserStateRepository
from flat_searcher.models import ListingPayload


class UserStateRepositoryTests(TestCase):
    def test_view_favorite_reject_and_notes_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _insert_listing(connection)
                user_states = UserStateRepository(connection)

                user_states.mark_viewed(listing_id, "2026-06-17T12:00:00+00:00")
                self.assertEqual(user_states.get_state(listing_id)["user_status"], "viewed")

                user_states.set_favorite(listing_id, True)
                favorite_state = user_states.get_state(listing_id)
                self.assertEqual(favorite_state["user_status"], "favorite")
                self.assertEqual(favorite_state["is_favorite"], 1)

                user_states.set_rejected(listing_id, True)
                rejected_state = user_states.get_state(listing_id)
                self.assertEqual(rejected_state["user_status"], "rejected")
                self.assertEqual(rejected_state["is_rejected"], 1)

                user_states.update_notes(listing_id, "Ask about land lease.")
                self.assertEqual(user_states.get_state(listing_id)["user_notes"], "Ask about land lease.")


def _insert_listing(connection) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id="test-1",
            ss_url="https://www.ss.com/msg/test.html",
            price_eur=100_000,
            area_m2=50,
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    return result.listing_id
