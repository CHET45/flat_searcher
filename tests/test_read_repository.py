import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.db import ListingReadRepository, ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.geo import AddressPrecision, GeocodeConfidence
from flat_searcher.models import ListingPayload


class ListingReadRepositoryTests(TestCase):
    def test_load_candidates_and_detail_from_persistent_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _seed_listing_with_analysis(connection)
                no_plan_listing_id = _seed_listing_with_empty_floor_plan_ids(connection)
                read_repository = ListingReadRepository(connection)

                candidates = read_repository.load_candidates("for_living_mortgage")
                detail = read_repository.load_detail(listing_id, "for_living_mortgage")

                self.assertEqual(len(candidates), 2)
                candidate = next(item for item in candidates if item.listing_id == listing_id)
                no_plan_candidate = next(
                    item for item in candidates if item.listing_id == no_plan_listing_id
                )
                self.assertEqual(candidate.score, 82.5)
                self.assertEqual(candidate.effective_private_rooms, 2)
                self.assertEqual(candidate.layout_confidence_label, LayoutConfidenceLabel.CONFIRMED)
                self.assertEqual(candidate.mortgage_risk_level, MortgageRiskLevel.LOW)
                self.assertTrue(candidate.has_floor_plan)
                self.assertFalse(no_plan_candidate.has_floor_plan)

                self.assertIsNotNone(detail)
                self.assertEqual(detail.overall_score, 82.5)
                self.assertEqual(detail.geocode_precision, AddressPrecision.EXACT_HOUSE)
                self.assertEqual(detail.geocode_confidence, GeocodeConfidence.HIGH)
                self.assertEqual(len(detail.history_snapshots), 1)

    def test_load_map_points_from_persistent_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                listing_id = _seed_listing_with_analysis(connection)
                points = ListingReadRepository(connection).load_map_points("for_living_mortgage")

                self.assertEqual(len(points), 1)
                self.assertEqual(points[0].listing_id, listing_id)
                self.assertEqual(points[0].address_precision, AddressPrecision.EXACT_HOUSE)
                self.assertEqual(points[0].score, 82.5)


def _seed_listing_with_analysis(connection) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id="test-1",
            ss_url="https://www.ss.com/msg/test.html",
            address_raw="Stabu 87",
            district="centrs",
            street="Stabu",
            house_number="87",
            price_eur=128_000,
            price_per_m2=2909.09,
            area_m2=44,
            declared_rooms_ss=2,
            description_text="Original listing text.",
            image_urls=("https://i.ss.com/gallery/test.jpg",),
            raw_html="<html>test</html>",
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    listing_id = result.listing_id
    connection.execute(
        """
        UPDATE listing_images
        SET is_floor_plan = 1
        WHERE listing_id = ?
        """,
        (listing_id,),
    )
    connection.execute(
        """
        INSERT INTO ai_analyses (
            listing_id, analysis_version, status, analyzed_at,
            effective_private_rooms, walkthrough_rooms, kitchen_living_detected,
            layout_confidence_label, ss_vs_ai_room_conflict, layout_explanation_user,
            floor_plan_image_ids, mortgage_risk_level, mortgage_risk_reasons,
            mortgage_explanation_user, stove_heating_risk, wooden_building_risk
        )
        VALUES (?, 'test-v1', 'finished', '2026-06-17T12:05:00+00:00',
                2, 0, 0, 'Confirmed', 0, 'Floor plan confirms the layout.',
                '[1]', 'Low', '[]', 'No major mortgage risk detected.', 0, 0)
        """,
        (listing_id,),
    )
    connection.execute(
        """
        INSERT INTO geocoding_results (
            listing_id, normalized_address, latitude, longitude, geocode_precision,
            geocode_confidence, geocode_source, geo_scores_enabled
        )
        VALUES (?, 'Stabu 87, Riga', 56.9517, 24.1383, 'exact_house', 'high', 'test', 1)
        """,
        (listing_id,),
    )
    connection.execute(
        """
        INSERT INTO location_scores (
            listing_id, distance_to_rtu_m, rtu_score, distance_to_central_station_m,
            station_score, transport_score, calculated_at
        )
        VALUES (?, 2500, 70, 1800, 85, 80, '2026-06-17T12:10:00+00:00')
        """,
        (listing_id,),
    )
    connection.execute(
        """
        INSERT INTO score_results (
            listing_id, profile_key, overall_score, score_breakdown_json,
            score_explanation, calculated_at
        )
        VALUES (?, 'for_living_mortgage', 82.5, '{}', 'Test score.', '2026-06-17T12:15:00+00:00')
        """,
        (listing_id,),
    )
    return listing_id


def _seed_listing_with_empty_floor_plan_ids(connection) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T13:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id="test-no-plan",
            ss_url="https://www.ss.com/msg/test-no-plan.html",
            district="Teika",
            street="Brivibas",
            house_number="1",
            price_eur=100_000,
            area_m2=50,
            declared_rooms_ss=2,
            description_text="Original listing text.",
            image_urls=("https://i.ss.com/gallery/no-plan.jpg",),
            raw_html="<html>test</html>",
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T13:00:01+00:00",
    )
    listing_id = result.listing_id
    connection.execute(
        """
        INSERT INTO ai_analyses (
            listing_id, analysis_version, status, analyzed_at,
            effective_private_rooms, walkthrough_rooms, kitchen_living_detected,
            layout_confidence_label, ss_vs_ai_room_conflict, layout_explanation_user,
            floor_plan_image_ids, mortgage_risk_level, mortgage_risk_reasons,
            mortgage_explanation_user, stove_heating_risk, wooden_building_risk
        )
        VALUES (?, 'test-v1', 'finished', '2026-06-17T13:05:00+00:00',
                2, 0, 0, 'Likely', 0, 'No floor plan was found.',
                '[]', 'Low', '[]', 'No major mortgage risk detected.', 0, 0)
        """,
        (listing_id,),
    )
    return listing_id
