import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, ListingReadRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.geo import Coordinate
from flat_searcher.geo.geocoder import GeocodeProviderResult
from flat_searcher.models import ListingPayload
from flat_searcher.services.geocoding import GeocodingService


class FakeGeocoder:
    def geocode(self, query: str) -> GeocodeProviderResult:
        return GeocodeProviderResult(
            coordinate=Coordinate(latitude=56.95, longitude=24.1),
            source="fake",
            explanation=f"Fake result for {query}",
        )


class GeocodingServiceTests(TestCase):
    def test_geocode_missing_persists_precision_confidence_and_score_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)

            with open_database(database_path) as connection:
                _insert_listing(connection, ss_id="exact", street="Stabu", house_number="87")
                _insert_listing(connection, ss_id="street", street="Brivibas", house_number=None)

            result = GeocodingService(database_path, FakeGeocoder()).geocode_missing()

            with open_database(database_path) as connection:
                points = ListingReadRepository(connection).load_map_points("for_living_mortgage")
                rows = connection.execute(
                    """
                    SELECT normalized_address, geocode_precision, geocode_confidence,
                           geo_scores_enabled, geo_scores_disabled_reason
                    FROM geocoding_results
                    ORDER BY listing_id
                    """
                ).fetchall()

            self.assertEqual(result.checked_count, 2)
            self.assertEqual(result.geocoded_count, 2)
            self.assertEqual(result.score_enabled_count, 1)
            self.assertEqual(len(points), 2)
            self.assertEqual(rows[0]["geocode_precision"], "exact_house")
            self.assertEqual(rows[0]["geocode_confidence"], "high")
            self.assertEqual(rows[0]["geo_scores_enabled"], 1)
            self.assertEqual(rows[1]["geocode_precision"], "street_approx")
            self.assertEqual(rows[1]["geocode_confidence"], "medium")
            self.assertEqual(rows[1]["geo_scores_enabled"], 0)
            self.assertEqual(
                rows[1]["geo_scores_disabled_reason"],
                "Approximate address - location scores not calculated",
            )


def _insert_listing(connection, ss_id: str, street: str, house_number: str | None) -> None:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    repository.upsert_listing(
        ListingPayload(
            ss_id=ss_id,
            ss_url=f"https://www.ss.com/msg/{ss_id}.html",
            district="centrs",
            street=street,
            house_number=house_number,
            price_eur=100_000,
            area_m2=50,
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
