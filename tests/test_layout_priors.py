import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.ai import build_pass2_prompt
from flat_searcher.db import LayoutPriorRepository, open_database
from flat_searcher.db.ai_repository import AIAnalysisRepository
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.layout_prior_repository import LayoutPrior
from flat_searcher.db.repository import ListingRepository
from flat_searcher.models import ListingPayload


class LayoutPriorRepositoryTests(TestCase):
    def test_init_database_seeds_priors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                count = LayoutPriorRepository(connection).count()

            self.assertGreater(count, 0)

    def test_seed_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = LayoutPriorRepository(connection)
                first = repository.count()
                seeded_again = repository.seed_default_priors()

            self.assertEqual(seeded_again, 0)
            self.assertGreater(first, 0)

    def test_find_candidates_prefers_series_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                candidates = LayoutPriorRepository(connection).find_candidates(
                    series_name="602",
                    building_type="panel",
                    area_m2=55.0,
                    room_count=3,
                )

            self.assertTrue(candidates)
            self.assertEqual(candidates[0].series_name, "602")

    def test_find_candidates_returns_empty_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                candidates = LayoutPriorRepository(connection).find_candidates(
                    series_name=None,
                    building_type=None,
                    area_m2=None,
                    room_count=None,
                )

            self.assertEqual(candidates, ())

    def test_custom_prior_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            prior = LayoutPrior(
                series_name="custom-series",
                building_type="brick",
                construction_period="1930s",
                typical_area_min=40.0,
                typical_area_max=70.0,
                typical_room_count=3,
                typical_layout_variants=("isolated rooms",),
                walkthrough_probability=0.2,
                isolated_rooms_probability=0.8,
                source_note="Manual entry.",
                confidence="high",
                verified=True,
            )
            with open_database(database_path) as connection:
                LayoutPriorRepository(connection).upsert_prior(prior)

            with open_database(database_path) as connection:
                candidates = LayoutPriorRepository(connection).find_candidates(
                    series_name="custom-series",
                    building_type="brick",
                    area_m2=55.0,
                    room_count=3,
                )

            self.assertEqual(candidates[0].confidence, "high")
            self.assertTrue(candidates[0].verified)


class LayoutPriorPromptTests(TestCase):
    def test_priors_are_passed_but_floor_plan_stays_primary(self) -> None:
        priors = (
            {"series": "602", "typical_room_count": 3, "confidence": "medium"},
        )
        prompt = build_pass2_prompt(
            listing_text="Spacious flat.",
            ss_fields={"declared_rooms_ss": 3},
            pass1_output={"floor_plan_image_ids": ["1"]},
            layout_priors=priors,
        )

        self.assertIn("layout_priors", prompt)
        self.assertIn("602", prompt)
        # The product rule: a floor plan, when present, is the primary source.
        self.assertIn("floor plan", prompt.lower())
        self.assertIn("primary layout source", prompt)


class LayoutPriorWiringTests(TestCase):
    def test_pending_listing_carries_relevant_priors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = ListingRepository(connection)
                run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
                repository.upsert_listing(
                    ListingPayload(
                        ss_id="prior-1",
                        ss_url="https://www.ss.com/msg/prior-1.html",
                        district="Teika",
                        price_eur=90_000,
                        area_m2=55.0,
                        declared_rooms_ss=3,
                        building_series="602",
                        building_type="panel",
                        description_text="Apartment in a 602 series building.",
                    ),
                    app_run_id=run_id,
                    checked_at="2026-06-17T12:00:01+00:00",
                )

            with open_database(database_path) as connection:
                listings = AIAnalysisRepository(connection).load_pending_listings()

            self.assertEqual(len(listings), 1)
            self.assertTrue(listings[0].layout_priors)
            self.assertTrue(
                any("602" == prior.get("series") for prior in listings[0].layout_priors)
            )
