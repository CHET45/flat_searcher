"""Geocoding and location scoring package."""

from flat_searcher.geo.address import (
    AddressPrecision,
    AddressScoreEligibility,
    GeocodeConfidence,
    determine_address_precision,
    location_score_eligibility,
)
from flat_searcher.geo.distance import Coordinate, haversine_distance_m
from flat_searcher.geo.location_scores import (
    ShopScoreInput,
    TransportScoreInput,
    central_station_distance_score,
    rtu_distance_score,
    shop_score,
    transport_score,
)
from flat_searcher.geo.geocoder import GeocodeProviderResult, Geocoder, NominatimGeocoder

__all__ = [
    "AddressPrecision",
    "AddressScoreEligibility",
    "Coordinate",
    "GeocodeConfidence",
    "GeocodeProviderResult",
    "Geocoder",
    "NominatimGeocoder",
    "ShopScoreInput",
    "TransportScoreInput",
    "central_station_distance_score",
    "determine_address_precision",
    "haversine_distance_m",
    "location_score_eligibility",
    "rtu_distance_score",
    "shop_score",
    "transport_score",
]
