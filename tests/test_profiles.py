import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, ProfileRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.models import ListingPayload
from flat_searcher.scoring import (
    ImportanceLevel,
    ScoreBlockKey,
    builtin_profiles,
    custom_profile,
    default_living_mortgage_profile,
    slugify_profile_name,
)
from flat_searcher.services.ai_analysis import AIAnalysisService, MockAIAnalysisProvider
from flat_searcher.services.scoring import ScoreRecalculationService


class BuiltinProfileTests(TestCase):
    def test_nine_preset_profiles_are_available(self) -> None:
        profiles = builtin_profiles()

        expected_keys = {
            "for_living_mortgage",
            "mortgage_first",
            "maximum_opportunity",
            "only_two_private_rooms",
            "best_price",
            "best_transport",
            "closer_to_rtu",
            "cash_purchase",
            "investment_option",
        }
        self.assertEqual(set(profiles), expected_keys)

    def test_profiles_cover_every_block_and_never_include_views(self) -> None:
        for profile in builtin_profiles().values():
            self.assertEqual(set(profile.block_importance), set(ScoreBlockKey))
            self.assertNotIn("views", {block.value for block in profile.block_importance})

    def test_preset_directions_match_spec(self) -> None:
        profiles = builtin_profiles()

        self.assertEqual(
            profiles["mortgage_first"].weight_for(ScoreBlockKey.MORTGAGE_SUITABILITY),
            ImportanceLevel.CRITICAL.weight,
        )
        self.assertEqual(
            profiles["best_price"].weight_for(ScoreBlockKey.PRICE_VALUE),
            ImportanceLevel.CRITICAL.weight,
        )
        self.assertEqual(
            profiles["closer_to_rtu"].weight_for(ScoreBlockKey.RTU_ACCESSIBILITY),
            ImportanceLevel.CRITICAL.weight,
        )
        # Cash purchase does not care about mortgage bankability.
        self.assertEqual(
            profiles["cash_purchase"].weight_for(ScoreBlockKey.MORTGAGE_SUITABILITY),
            ImportanceLevel.IGNORE.weight,
        )

    def test_default_profile_keeps_floor_and_condition_disabled(self) -> None:
        profile = default_living_mortgage_profile()

        self.assertEqual(profile.weight_for(ScoreBlockKey.FLOOR), 0)
        self.assertEqual(profile.weight_for(ScoreBlockKey.CONDITION_RENOVATION), 0)

    def test_slugify_profile_name(self) -> None:
        self.assertEqual(slugify_profile_name("My Best Picks!"), "custom_my_best_picks")
        self.assertEqual(slugify_profile_name("   "), "custom_profile")


class ProfileRepositoryTests(TestCase):
    def test_sync_and_load_builtin_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = ProfileRepository(connection)
                repository.sync_builtin_profiles()
                summaries = repository.list_profiles()
                loaded = repository.load_profile("mortgage_first")

            self.assertEqual(len(summaries), len(builtin_profiles()))
            self.assertTrue(all(summary.is_builtin for summary in summaries))
            self.assertEqual(
                loaded.weight_for(ScoreBlockKey.MORTGAGE_SUITABILITY),
                ImportanceLevel.CRITICAL.weight,
            )

    def test_custom_profile_round_trips_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            profile = custom_profile(
                key="custom_quiet",
                name="Quiet street",
                importance={
                    ScoreBlockKey.PRICE_VALUE: ImportanceLevel.STRONG,
                    ScoreBlockKey.ROOM_PRIVACY: ImportanceLevel.CRITICAL,
                },
                base_profile_key="for_living_mortgage",
            )

            with open_database(database_path) as connection:
                repository = ProfileRepository(connection)
                repository.sync_builtin_profiles()
                repository.save_profile(profile)

            with open_database(database_path) as connection:
                repository = ProfileRepository(connection)
                loaded = repository.load_profile("custom_quiet")
                summaries = repository.list_profiles()

            self.assertIsNotNone(loaded)
            self.assertFalse(loaded.is_builtin)
            self.assertEqual(loaded.base_profile_key, "for_living_mortgage")
            self.assertEqual(
                loaded.weight_for(ScoreBlockKey.ROOM_PRIVACY),
                ImportanceLevel.CRITICAL.weight,
            )
            self.assertIn("custom_quiet", {summary.profile_key for summary in summaries})

            with open_database(database_path) as connection:
                repository = ProfileRepository(connection)
                repository.delete_profile("custom_quiet")
                self.assertIsNone(repository.load_profile("custom_quiet"))

    def test_delete_does_not_remove_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = ProfileRepository(connection)
                repository.sync_builtin_profiles()
                repository.delete_profile("for_living_mortgage")
                self.assertIsNotNone(repository.load_profile("for_living_mortgage"))


class ProfileScoringTests(TestCase):
    def test_recalculate_supports_non_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                _insert_listing(connection, "p-1", 90_000, 50)
                _insert_listing(connection, "p-2", 130_000, 60)

            AIAnalysisService(
                database_path=database_path,
                provider=MockAIAnalysisProvider(),
            ).analyze_pending(analysis_version="mock-profile-test")

            result = ScoreRecalculationService(database_path).recalculate("best_price")

            with open_database(database_path) as connection:
                rows = connection.execute(
                    "SELECT profile_key FROM score_results GROUP BY profile_key"
                ).fetchall()

            self.assertEqual(result.scored_count, 2)
            self.assertEqual({row["profile_key"] for row in rows}, {"best_price"})

    def test_recalculate_rejects_unknown_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with self.assertRaises(ValueError):
                ScoreRecalculationService(database_path).recalculate("does_not_exist")


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
