"""Serializable apartment marker payloads for the map view."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from flat_searcher.geo import AddressPrecision


class MarkerVisualState(StrEnum):
    NORMAL = "normal"
    APPROXIMATE = "approximate"
    DISTRICT = "district"
    FAVORITE = "favorite"
    REJECTED = "rejected"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class MapApartmentPoint:
    listing_id: int
    latitude: float | None
    longitude: float | None
    address_precision: AddressPrecision
    score: float | None
    is_favorite: bool = False
    is_rejected: bool = False
    listing_status: str = "active"


@dataclass(frozen=True)
class MapMarker:
    listing_id: int
    latitude: float
    longitude: float
    visual_state: MarkerVisualState
    score_bucket: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["visual_state"] = self.visual_state.value
        return data


@dataclass(frozen=True)
class MapReferencePoint:
    point_id: str
    latitude: float
    longitude: float
    kind: str
    title: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_map_markers(points: tuple[MapApartmentPoint, ...]) -> tuple[MapMarker, ...]:
    markers = []
    for point in points:
        if point.latitude is None or point.longitude is None:
            continue
        markers.append(
            MapMarker(
                listing_id=point.listing_id,
                latitude=point.latitude,
                longitude=point.longitude,
                visual_state=_visual_state(point),
                score_bucket=_score_bucket(point.score),
            )
        )
    return tuple(markers)


def _visual_state(point: MapApartmentPoint) -> MarkerVisualState:
    if point.listing_status == "inactive":
        return MarkerVisualState.INACTIVE
    if point.is_rejected:
        return MarkerVisualState.REJECTED
    if point.is_favorite:
        return MarkerVisualState.FAVORITE
    if point.address_precision == AddressPrecision.STREET_APPROX:
        return MarkerVisualState.APPROXIMATE
    if point.address_precision == AddressPrecision.DISTRICT_APPROX:
        return MarkerVisualState.DISTRICT
    return MarkerVisualState.NORMAL


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "low"
    return "very_low"
