"""Nearby grocery and public transport POIs from Overpass."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flat_searcher.geo.distance import Coordinate


class POICategory(StrEnum):
    GROCERY_SHOP = "grocery_shop"
    TRANSPORT_STOP = "transport_stop"


@dataclass(frozen=True)
class NearbyPOI:
    osm_element_type: str
    osm_element_id: int
    category: POICategory
    coordinate: Coordinate
    name: str | None
    tags: dict[str, str]


class POIProvider(Protocol):
    def fetch_nearby(
        self,
        coordinate: Coordinate,
        radius_m: int,
    ) -> tuple[NearbyPOI, ...]: ...


class OverpassTransport(Protocol):
    def post_json(self, endpoint: str, query: str) -> dict[str, object]: ...


class OverpassError(RuntimeError):
    pass


class UrlLibOverpassTransport:
    def __init__(
        self,
        timeout_seconds: float = 45.0,
        user_agent: str = "FlatSearcher/0.1 (+local desktop apartment analysis)",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def post_json(self, endpoint: str, query: str) -> dict[str, object]:
        request = Request(
            endpoint,
            data=urlencode({"data": query}).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OverpassError(f"Overpass request failed: {error}") from error
        if not isinstance(payload, dict):
            raise OverpassError("Overpass response must be a JSON object.")
        return payload


class OverpassPOIProvider:
    def __init__(
        self,
        endpoint: str,
        transport: OverpassTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.transport = transport or UrlLibOverpassTransport()

    def fetch_nearby(
        self,
        coordinate: Coordinate,
        radius_m: int,
    ) -> tuple[NearbyPOI, ...]:
        if radius_m <= 0:
            raise ValueError("Overpass radius must be positive.")
        payload = self.transport.post_json(
            self.endpoint,
            _build_query(coordinate, radius_m),
        )
        return _parse_pois(payload)


def _build_query(coordinate: Coordinate, radius_m: int) -> str:
    center = f"{radius_m},{coordinate.latitude:.7f},{coordinate.longitude:.7f}"
    return "\n".join(
        (
            "[out:json][timeout:30];",
            "(",
            f'  nwr(around:{center})["shop"~"^(supermarket|convenience|grocery)$"];',
            f'  nwr(around:{center})["highway"="bus_stop"];',
            f'  nwr(around:{center})["public_transport"="platform"];',
            f'  nwr(around:{center})["railway"~"^(tram_stop|halt|station)$"];',
            ");",
            "out center tags;",
        )
    )


def _parse_pois(payload: dict[str, object]) -> tuple[NearbyPOI, ...]:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise OverpassError("Overpass response is missing the elements list.")

    pois: dict[tuple[str, int, POICategory], NearbyPOI] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_type = element.get("type")
        element_id = element.get("id")
        if not isinstance(element_type, str) or not isinstance(element_id, int):
            continue
        coordinate = _element_coordinate(element)
        tags = _string_tags(element.get("tags"))
        if coordinate is None or not tags:
            continue
        for category in _categories(tags):
            key = (element_type, element_id, category)
            pois[key] = NearbyPOI(
                osm_element_type=element_type,
                osm_element_id=element_id,
                category=category,
                coordinate=coordinate,
                name=tags.get("name") or tags.get("brand"),
                tags=tags,
            )
    return tuple(pois.values())


def _element_coordinate(element: dict[str, object]) -> Coordinate | None:
    latitude = element.get("lat")
    longitude = element.get("lon")
    if not isinstance(latitude, int | float) or not isinstance(
        longitude,
        int | float,
    ):
        center = element.get("center")
        if not isinstance(center, dict):
            return None
        latitude = center.get("lat")
        longitude = center.get("lon")
    if not isinstance(latitude, int | float) or not isinstance(
        longitude,
        int | float,
    ):
        return None
    return Coordinate(float(latitude), float(longitude))


def _string_tags(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _categories(tags: dict[str, str]) -> tuple[POICategory, ...]:
    result = []
    if tags.get("shop") in {"supermarket", "convenience", "grocery"}:
        result.append(POICategory.GROCERY_SHOP)
    if (
        tags.get("highway") == "bus_stop"
        or tags.get("public_transport") == "platform"
        or tags.get("railway") in {"tram_stop", "halt", "station"}
    ):
        result.append(POICategory.TRANSPORT_STOP)
    return tuple(result)
