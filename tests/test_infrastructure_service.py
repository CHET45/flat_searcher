import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.geocoding_repository import GeocodingRepository
from flat_searcher.geo import (
    AddressPrecision,
    Coordinate,
    GeocodeConfidence,
    NearbyPOI,
    POICategory,
)
from flat_searcher.models import ListingPayload
from flat_searcher.services.infrastructure import InfrastructureRefreshService
from flat_searcher.services.location_scoring import LocationScoreService


ORIGIN = Coordinate(56.95, 24.1)


class FakePOIProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def fetch_nearby(
        self,
        coordinate: Coordinate,
        radius_m: int,
    ) -> tuple[NearbyPOI, ...]:
        self.call_count += 1
        self.last_coordinate = coordinate
        self.last_radius_m = radius_m
        return (
            _poi(1, POICategory.GROCERY_SHOP, 0.001),
            _poi(2, POICategory.GROCERY_SHOP, 0.008),
            _poi(3, POICategory.GROCERY_SHOP, 0.016),
            _poi(4, POICategory.TRANSPORT_STOP, 0.003),
            _poi(5, POICategory.TRANSPORT_STOP, 0.010),
            _poi(6, POICategory.TRANSPORT_STOP, 0.020),
        )


class InfrastructureRefreshServiceTests(TestCase):
    def test_refresh_caches_pois_and_populates_shop_and_transport_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)
            with open_database(database_path) as connection:
                listing_id = _insert_exact_listing(connection)

            provider = FakePOIProvider()
            service = InfrastructureRefreshService(
                database_path=database_path,
                provider=provider,
                source_endpoint="https://overpass.test/api",
            )

            first_refresh = service.refresh()
            cached_refresh = service.refresh()
            location_result = LocationScoreService(database_path).recalculate()

            with open_database(database_path) as connection:
                score = connection.execute(
                    """
                    SELECT nearest_shop_distance_m, shops_within_300m,
                           shops_within_700m, shops_within_1200m, shop_score,
                           nearest_transport_stop_distance_m,
                           transport_stops_nearby_count, transport_score
                    FROM location_scores
                    WHERE listing_id = ?
                    """,
                    (listing_id,),
                ).fetchone()
                link_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM osm_listing_pois
                    WHERE listing_id = ?
                    """,
                    (listing_id,),
                ).fetchone()[0]

            self.assertEqual(first_refresh.refreshed_count, 1)
            self.assertEqual(first_refresh.poi_count, 6)
            self.assertEqual(cached_refresh.cached_count, 1)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(location_result.calculated_count, 1)
            self.assertEqual(link_count, 6)
            self.assertEqual(score["shops_within_300m"], 1)
            self.assertEqual(score["shops_within_700m"], 2)
            self.assertEqual(score["shops_within_1200m"], 3)
            self.assertGreater(score["shop_score"], 0)
            self.assertEqual(score["transport_stops_nearby_count"], 2)
            self.assertGreater(score["transport_score"], 0)


def _insert_exact_listing(connection) -> int:
    repository = ListingRepository(connection)
    run_id = repository.create_app_run("test", "2026-06-17T12:00:00+00:00")
    result = repository.upsert_listing(
        ListingPayload(
            ss_id="infrastructure-test",
            ss_url="https://www.ss.com/msg/infrastructure-test.html",
            district="centrs",
            street="Testa iela",
            house_number="1",
            price_eur=100_000,
            area_m2=50,
        ),
        app_run_id=run_id,
        checked_at="2026-06-17T12:00:01+00:00",
    )
    GeocodingRepository(connection).upsert_geocoding_result(
        listing_id=result.listing_id,
        normalized_address="Testa iela 1, Riga, Latvia",
        latitude=ORIGIN.latitude,
        longitude=ORIGIN.longitude,
        precision=AddressPrecision.EXACT_HOUSE,
        confidence=GeocodeConfidence.HIGH,
        source="test",
        explanation="Exact test point.",
        geo_scores_enabled=True,
        disabled_reason=None,
    )
    return result.listing_id


def _poi(
    osm_id: int,
    category: POICategory,
    longitude_offset: float,
) -> NearbyPOI:
    return NearbyPOI(
        osm_element_type="node",
        osm_element_id=osm_id,
        category=category,
        coordinate=Coordinate(
            latitude=ORIGIN.latitude,
            longitude=ORIGIN.longitude + longitude_offset,
        ),
        name=f"POI {osm_id}",
        tags={},
    )
