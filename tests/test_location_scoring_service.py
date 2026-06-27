import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.geocoding_repository import GeocodingRepository
from flat_searcher.geo import AddressPrecision, GeocodeConfidence
from flat_searcher.models import ListingPayload
from flat_searcher.services.location_scoring import LocationScoreService


class LocationScoreServiceTests(TestCase):
    def test_recalculate_persists_exact_scores_and_disables_approximate_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                exact_id = _insert_listing(connection, "exact-location")
                approximate_id = _insert_listing(connection, "approximate-location")
                geocoding = GeocodingRepository(connection)
                geocoding.upsert_geocoding_result(
                    listing_id=exact_id,
                    normalized_address="Kipsalas iela 6A, Riga, Latvia",
                    latitude=56.9505,
                    longitude=24.0837,
                    precision=AddressPrecision.EXACT_HOUSE,
                    confidence=GeocodeConfidence.HIGH,
                    source="test",
                    explanation="Exact test point.",
                    geo_scores_enabled=True,
                    disabled_reason=None,
                )
                geocoding.upsert_geocoding_result(
                    listing_id=approximate_id,
                    normalized_address="Brivibas iela, Riga, Latvia",
                    latitude=56.96,
                    longitude=24.12,
                    precision=AddressPrecision.STREET_APPROX,
                    confidence=GeocodeConfidence.MEDIUM,
                    source="test",
                    explanation="Approximate test point.",
                    geo_scores_enabled=False,
                    disabled_reason="Approximate address - location scores not calculated",
                )

            first_result = LocationScoreService(database_path).recalculate()
            second_result = LocationScoreService(database_path).recalculate()

            with open_database(database_path) as connection:
                rows = connection.execute(
                    """
                    SELECT listing_id, distance_to_rtu_m, rtu_score,
                           distance_to_central_station_m, station_score, explanation
                    FROM location_scores
                    ORDER BY listing_id
                    """
                ).fetchall()

            self.assertEqual(first_result.listing_count, 2)
            self.assertEqual(first_result.calculated_count, 1)
            self.assertEqual(first_result.disabled_count, 1)
            self.assertEqual(second_result, first_result)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["listing_id"], exact_id)
            self.assertEqual(rows[0]["distance_to_rtu_m"], 0.0)
            self.assertEqual(rows[0]["rtu_score"], 100.0)
            self.assertIsNotNone(rows[0]["distance_to_central_station_m"])
            self.assertIsNotNone(rows[0]["station_score"])
            self.assertEqual(rows[1]["listing_id"], approximate_id)
            self.assertIsNone(rows[1]["distance_to_rtu_m"])
            self.assertIsNone(rows[1]["rtu_score"])
            self.assertEqual(
                rows[1]["explanation"],
                "Approximate address - location scores not calculated",
            )


def _insert_listing(connection, ss_id: str) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id=ss_id,
            ss_url=f"https://www.ss.com/msg/{ss_id}.html",
            district="Kipsala",
            street="Kipsalas iela",
            house_number="6A",
            price_eur=100_000,
            area_m2=50,
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    return result.listing_id
