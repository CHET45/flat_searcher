import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.db import (
    ListingReadRepository,
    ListingRepository,
    SearchSessionRepository,
    UserStateRepository,
    open_database,
)
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.read_models import ListingDetailReadModel
from flat_searcher.filtering import (
    ListingFilters,
    filters_from_dict,
    filters_to_dict,
)
from flat_searcher.models import ListingPayload
from flat_searcher.presentation import (
    build_comparison_view,
    ranking_row_view_model,
)
from flat_searcher.presentation.comparison import comparison_flags
from flat_searcher.ranking import rank_candidates


def _detail(listing_id: int, **overrides) -> ListingDetailReadModel:
    base = dict(
        listing_id=listing_id,
        ss_id=f"ss-{listing_id}",
        ss_url=f"https://www.ss.com/msg/{listing_id}.html",
        listing_status="active",
        user_status="unseen",
        is_favorite=False,
        is_rejected=False,
        is_viewed=False,
        user_notes=None,
        district="Teika",
        street="Brivibas iela",
        house_number="100",
        address_raw=None,
        price_eur=100_000,
        price_per_m2=2000.0,
        area_m2=50.0,
        declared_rooms_ss=2,
        floor=3,
        total_floors=5,
        building_series="602",
        building_type="panel",
        listing_date_text=None,
        unique_visits=None,
        description_text="Text",
        effective_private_rooms=2,
        walkthrough_rooms=0,
        kitchen_living_detected=False,
        layout_confidence_label=LayoutConfidenceLabel.LIKELY,
        layout_explanation_user="Two private rooms.",
        mortgage_risk_level=MortgageRiskLevel.LOW,
        mortgage_risk_reasons=None,
        mortgage_explanation_user=None,
        latitude=56.9,
        longitude=24.1,
        geocode_precision=None,
        geocode_confidence=None,
        geo_scores_enabled=True,
        geo_scores_disabled_reason=None,
        distance_to_rtu_m=1200.0,
        rtu_score=70.0,
        distance_to_central_station_m=3000.0,
        station_score=40.0,
        nearest_shop_distance_m=200.0,
        shops_within_300m=2,
        shops_within_700m=5,
        shops_within_1200m=9,
        shop_score=80.0,
        nearest_transport_stop_distance_m=150.0,
        transport_stops_nearby_count=4,
        transport_score=75.0,
        location_explanation="Calculated.",
        overall_score=78.0,
        history_snapshots=(),
        change_events=(),
    )
    base.update(overrides)
    return ListingDetailReadModel(**base)


class ComparisonViewTests(TestCase):
    def test_builds_rows_for_each_apartment(self) -> None:
        view = build_comparison_view((_detail(1), _detail(2, price_eur=120_000)))

        self.assertEqual(len(view.columns), 2)
        labels = {row.label for row in view.rows}
        self.assertIn("Price", labels)
        self.assertIn("Mortgage risk", labels)
        price_row = next(row for row in view.rows if row.label == "Price")
        self.assertEqual(price_row.values[0], "100 000 EUR")
        self.assertEqual(price_row.values[1], "120 000 EUR")

    def test_rejects_too_few_or_too_many(self) -> None:
        with self.assertRaises(ValueError):
            build_comparison_view((_detail(1),))
        with self.assertRaises(ValueError):
            build_comparison_view(tuple(_detail(i) for i in range(6)))

    def test_room_conflict_flag_from_room_mismatch(self) -> None:
        flags = comparison_flags(_detail(1, effective_private_rooms=2, declared_rooms_ss=3))

        self.assertIn("Room conflict", flags)


class FilterSerializationTests(TestCase):
    def test_round_trips_through_dict(self) -> None:
        filters = ListingFilters(
            price_min=80_000,
            price_max=150_000,
            area_min=40.0,
            districts=frozenset({"Teika", "Centrs"}),
            declared_rooms=frozenset({2, 3}),
            only_confirmed_layout=True,
            hide_viewed=True,
        )

        restored = filters_from_dict(filters_to_dict(filters))

        self.assertEqual(restored, filters)

    def test_ignores_unknown_keys(self) -> None:
        restored = filters_from_dict({"price_min": 50_000, "unknown_field": 1})

        self.assertEqual(restored.price_min, 50_000)


class SearchSessionRepositoryTests(TestCase):
    def test_save_load_and_overwrite_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            filters = ListingFilters(price_max=120_000, districts=frozenset({"Teika"}))
            with open_database(database_path) as connection:
                repository = SearchSessionRepository(connection)
                first_id = repository.save_session(
                    "My search", "best_price", filters, sort_mode="score_desc"
                )
                # Same name updates in place rather than duplicating.
                second_id = repository.save_session(
                    "My search", "for_living_mortgage", filters
                )

            with open_database(database_path) as connection:
                repository = SearchSessionRepository(connection)
                summaries = repository.list_sessions()
                loaded = repository.load_session(first_id)

            self.assertEqual(first_id, second_id)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(loaded.selected_profile_key, "for_living_mortgage")
            self.assertEqual(loaded.filters.price_max, 120_000)
            self.assertEqual(loaded.filters.districts, frozenset({"Teika"}))

    def test_delete_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = SearchSessionRepository(connection)
                session_id = repository.save_session(
                    "Temp", None, ListingFilters()
                )
                repository.delete_session(session_id)
                self.assertIsNone(repository.load_session(session_id))


class NoteIndicatorTests(TestCase):
    def test_notes_appear_as_ranking_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "db.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                repository = ListingRepository(connection)
                run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
                result = repository.upsert_listing(
                    ListingPayload(
                        ss_id="note-1",
                        ss_url="https://www.ss.com/msg/note-1.html",
                        district="Teika",
                        price_eur=90_000,
                        area_m2=50.0,
                        declared_rooms_ss=2,
                        description_text="Text",
                    ),
                    app_run_id=run_id,
                    checked_at="2026-06-17T12:00:01+00:00",
                )
                UserStateRepository(connection).update_notes(result.listing_id, "Call seller")

            with open_database(database_path) as connection:
                candidates = ListingReadRepository(connection).load_candidates(
                    "for_living_mortgage"
                )

            self.assertTrue(candidates[0].has_notes)
            ranked = rank_candidates(candidates, ListingFilters())
            row = ranking_row_view_model(ranked[0])
            self.assertIn("Note", row.flags_text)
