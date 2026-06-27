"""Refresh cached OSM grocery and public transport infrastructure."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flat_searcher.db.location_repository import LocationScoreRepository
from flat_searcher.db.poi_repository import POICacheRepository, POIFetchState
from flat_searcher.db.repository import open_database
from flat_searcher.geo import Coordinate, POIProvider, haversine_distance_m


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InfrastructureRefreshResult:
    eligible_count: int
    refreshed_count: int
    cached_count: int
    failed_count: int
    poi_count: int


class InfrastructureRefreshService:
    def __init__(
        self,
        database_path: Path,
        provider: POIProvider,
        source_endpoint: str,
    ) -> None:
        self.database_path = database_path
        self.provider = provider
        self.source_endpoint = source_endpoint

    def refresh(
        self,
        radius_m: int = 1_800,
        max_age_hours: float = 168,
        limit: int | None = None,
        force: bool = False,
    ) -> InfrastructureRefreshResult:
        if radius_m < 1_800:
            raise ValueError("Infrastructure radius must be at least 1800 meters.")
        now = datetime.now(UTC)
        with open_database(self.database_path) as connection:
            listings = LocationScoreRepository(connection).load_geocoded_listings()
            eligible = tuple(
                listing
                for listing in listings
                if listing.geo_scores_enabled
                and listing.latitude is not None
                and listing.longitude is not None
            )
            if limit is not None:
                eligible = eligible[:limit]
            repository = POICacheRepository(connection)
            refreshed_count = 0
            cached_count = 0
            failed_count = 0
            poi_count = 0

            for listing in eligible:
                origin = Coordinate(listing.latitude, listing.longitude)
                state = repository.load_fetch_state(listing.listing_id)
                if not force and _is_fresh(
                    state,
                    origin,
                    radius_m,
                    max_age_hours,
                    now,
                    self.source_endpoint,
                ):
                    cached_count += 1
                    continue
                try:
                    pois = self.provider.fetch_nearby(origin, radius_m)
                    repository.replace_listing_pois(
                        listing_id=listing.listing_id,
                        origin=origin,
                        radius_m=radius_m,
                        fetched_at=now.isoformat(timespec="seconds"),
                        source_endpoint=self.source_endpoint,
                        pois_with_distance=tuple(
                            (
                                poi,
                                haversine_distance_m(origin, poi.coordinate),
                            )
                            for poi in pois
                        ),
                    )
                    refreshed_count += 1
                    poi_count += len(pois)
                except Exception as error:
                    LOGGER.warning(
                        "Infrastructure refresh failed for listing %s: %s",
                        listing.listing_id,
                        error,
                    )
                    failed_count += 1

            return InfrastructureRefreshResult(
                eligible_count=len(eligible),
                refreshed_count=refreshed_count,
                cached_count=cached_count,
                failed_count=failed_count,
                poi_count=poi_count,
            )


def _is_fresh(
    state: POIFetchState | None,
    origin: Coordinate,
    radius_m: int,
    max_age_hours: float,
    now: datetime,
    source_endpoint: str,
) -> bool:
    if state is None or max_age_hours <= 0:
        return False
    try:
        fetched_at = datetime.fromisoformat(state.fetched_at)
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return (
        abs(state.latitude - origin.latitude) < 0.000001
        and abs(state.longitude - origin.longitude) < 0.000001
        and state.radius_m >= radius_m
        and state.source_endpoint == source_endpoint
        and now - fetched_at <= timedelta(hours=max_age_hours)
    )
