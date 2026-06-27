"""Distance-based location score recalculation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from flat_searcher.db.location_repository import LocationScoreRepository
from flat_searcher.db.poi_repository import InfrastructureSummary, POICacheRepository
from flat_searcher.db.repository import open_database
from flat_searcher.geo import (
    Coordinate,
    ShopScoreInput,
    TransportScoreInput,
    central_station_distance_score,
    haversine_distance_m,
    rtu_distance_score,
    shop_score,
    transport_score,
)


RTU_MAIN_POINT = Coordinate(latitude=56.9505, longitude=24.0837)
RIGA_CENTRAL_STATION_POINT = Coordinate(latitude=56.9463, longitude=24.1209)


@dataclass(frozen=True)
class LocationScoreRunResult:
    listing_count: int
    calculated_count: int
    disabled_count: int


class LocationScoreService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def recalculate(self) -> LocationScoreRunResult:
        with open_database(self.database_path) as connection:
            repository = LocationScoreRepository(connection)
            poi_repository = POICacheRepository(connection)
            listings = repository.load_geocoded_listings()
            calculated_count = 0
            disabled_count = 0

            for listing in listings:
                if (
                    not listing.geo_scores_enabled
                    or listing.latitude is None
                    or listing.longitude is None
                ):
                    repository.save_location_scores(
                        listing_id=listing.listing_id,
                        distance_to_rtu_m=None,
                        rtu_score=None,
                        distance_to_station_m=None,
                        station_score=None,
                        nearest_shop_distance_m=None,
                        shops_within_300m=None,
                        shops_within_700m=None,
                        shops_within_1200m=None,
                        shop_score=None,
                        nearest_transport_stop_distance_m=None,
                        transport_stops_nearby_count=None,
                        transport_score=None,
                        calculated_at=_now(),
                        explanation=listing.disabled_reason
                        or "Location scores are disabled for this address.",
                    )
                    disabled_count += 1
                    continue

                apartment = Coordinate(
                    latitude=listing.latitude,
                    longitude=listing.longitude,
                )
                rtu_distance = haversine_distance_m(apartment, RTU_MAIN_POINT)
                station_distance = haversine_distance_m(
                    apartment,
                    RIGA_CENTRAL_STATION_POINT,
                )
                infrastructure = poi_repository.load_infrastructure_summary(
                    listing.listing_id
                )
                shop_score_value = _shop_score(infrastructure)
                transport_score_value = _transport_score(infrastructure)
                repository.save_location_scores(
                    listing_id=listing.listing_id,
                    distance_to_rtu_m=round(rtu_distance, 1),
                    rtu_score=round(rtu_distance_score(rtu_distance) or 0.0, 2),
                    distance_to_station_m=round(station_distance, 1),
                    station_score=round(
                        central_station_distance_score(station_distance) or 0.0,
                        2,
                    ),
                    nearest_shop_distance_m=(
                        infrastructure.nearest_shop_distance_m
                        if infrastructure.has_cache
                        else None
                    ),
                    shops_within_300m=(
                        infrastructure.shops_within_300m
                        if infrastructure.has_cache
                        else None
                    ),
                    shops_within_700m=(
                        infrastructure.shops_within_700m
                        if infrastructure.has_cache
                        else None
                    ),
                    shops_within_1200m=(
                        infrastructure.shops_within_1200m
                        if infrastructure.has_cache
                        else None
                    ),
                    shop_score=shop_score_value,
                    nearest_transport_stop_distance_m=(
                        infrastructure.nearest_transport_stop_distance_m
                        if infrastructure.has_cache
                        else None
                    ),
                    transport_stops_nearby_count=(
                        infrastructure.transport_stops_within_900m
                        if infrastructure.has_cache
                        else None
                    ),
                    transport_score=transport_score_value,
                    calculated_at=_now(),
                    explanation=_explanation(infrastructure),
                )
                calculated_count += 1

            return LocationScoreRunResult(
                listing_count=len(listings),
                calculated_count=calculated_count,
                disabled_count=disabled_count,
            )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _shop_score(infrastructure: InfrastructureSummary) -> float | None:
    if not infrastructure.has_cache:
        return None
    if infrastructure.nearest_shop_distance_m is None:
        return 0.0
    value = shop_score(
        ShopScoreInput(
            nearest_shop_distance_m=infrastructure.nearest_shop_distance_m,
            shops_within_300m=infrastructure.shops_within_300m,
            shops_within_700m=infrastructure.shops_within_700m,
            shops_within_1200m=infrastructure.shops_within_1200m,
        )
    )
    return None if value is None else round(value, 2)


def _transport_score(infrastructure: InfrastructureSummary) -> float | None:
    if not infrastructure.has_cache:
        return None
    if infrastructure.nearest_transport_stop_distance_m is None:
        return 0.0
    value = transport_score(
        TransportScoreInput(
            nearest_stop_distance_m=(
                infrastructure.nearest_transport_stop_distance_m
            ),
            stops_nearby_count=infrastructure.transport_stops_within_900m,
        )
    )
    return None if value is None else round(value, 2)


def _explanation(infrastructure: InfrastructureSummary) -> str:
    destination_text = (
        "Straight-line distance scores for RTU main campus and "
        "Riga Central Station / Origo."
    )
    if not infrastructure.has_cache:
        return destination_text + " OSM infrastructure cache is not available."
    return (
        f"{destination_text} OSM cache contains "
        f"{infrastructure.shops_within_1200m} grocery shops within 1200 m and "
        f"{infrastructure.transport_stops_within_900m} transport stops within 900 m."
    )
