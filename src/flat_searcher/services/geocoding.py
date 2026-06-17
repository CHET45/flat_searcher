"""Geocoding service for listing addresses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flat_searcher.db.geocoding_repository import GeocodingRepository, ListingAddressRecord
from flat_searcher.db.repository import open_database
from flat_searcher.geo import (
    AddressPrecision,
    GeocodeConfidence,
    determine_address_precision,
    location_score_eligibility,
)
from flat_searcher.geo.geocoder import Geocoder


@dataclass(frozen=True)
class GeocodingRunResult:
    checked_count: int
    geocoded_count: int
    score_enabled_count: int


class GeocodingService:
    def __init__(self, database_path: Path, geocoder: Geocoder) -> None:
        self.database_path = database_path
        self.geocoder = geocoder

    def geocode_missing(self, limit: int | None = None) -> GeocodingRunResult:
        with open_database(self.database_path) as connection:
            repository = GeocodingRepository(connection)
            records = repository.load_ungeocoded_addresses(limit)
            geocoded_count = 0
            score_enabled_count = 0
            for record in records:
                geocoded, scores_enabled = self._geocode_record(repository, record)
                geocoded_count += 1 if geocoded else 0
                score_enabled_count += 1 if scores_enabled else 0
            return GeocodingRunResult(
                checked_count=len(records),
                geocoded_count=geocoded_count,
                score_enabled_count=score_enabled_count,
            )

    def _geocode_record(
        self,
        repository: GeocodingRepository,
        record: ListingAddressRecord,
    ) -> tuple[bool, bool]:
        precision = determine_address_precision(
            district=record.district,
            street=record.street,
            house_number=record.house_number,
        )
        query = _query_for(record, precision)
        provider_result = self.geocoder.geocode(query)
        confidence = _confidence_for(precision, provider_result.coordinate is not None)
        eligibility = location_score_eligibility(precision, confidence)
        repository.upsert_geocoding_result(
            listing_id=record.listing_id,
            normalized_address=query,
            latitude=None if provider_result.coordinate is None else provider_result.coordinate.latitude,
            longitude=None if provider_result.coordinate is None else provider_result.coordinate.longitude,
            precision=precision,
            confidence=confidence,
            source=provider_result.source,
            explanation=provider_result.explanation,
            geo_scores_enabled=eligibility.geo_scores_enabled,
            disabled_reason=eligibility.disabled_reason,
        )
        return provider_result.coordinate is not None, eligibility.geo_scores_enabled


def _query_for(record: ListingAddressRecord, precision: AddressPrecision) -> str:
    if precision == AddressPrecision.EXACT_HOUSE:
        return f"{record.street} {record.house_number}, Riga, Latvia"
    if precision == AddressPrecision.STREET_APPROX:
        return f"{record.street}, Riga, Latvia"
    if precision == AddressPrecision.DISTRICT_APPROX:
        return f"{record.district}, Riga, Latvia"
    return "Riga, Latvia"


def _confidence_for(
    precision: AddressPrecision,
    has_coordinate: bool,
) -> GeocodeConfidence | None:
    if not has_coordinate:
        return None
    if precision == AddressPrecision.EXACT_HOUSE:
        return GeocodeConfidence.HIGH
    if precision == AddressPrecision.STREET_APPROX:
        return GeocodeConfidence.MEDIUM
    if precision == AddressPrecision.DISTRICT_APPROX:
        return GeocodeConfidence.LOW
    return None
