"""Geocoding provider abstractions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flat_searcher.geo.distance import Coordinate


@dataclass(frozen=True)
class GeocodeProviderResult:
    coordinate: Coordinate | None
    source: str
    explanation: str


class Geocoder(Protocol):
    def geocode(self, query: str) -> GeocodeProviderResult: ...


class NominatimGeocoder:
    def __init__(
        self,
        user_agent: str = "FlatSearcher/0.1 local desktop app",
        endpoint: str = "https://nominatim.openstreetmap.org/search",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.user_agent = user_agent
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def geocode(self, query: str) -> GeocodeProviderResult:
        params = urlencode({"q": query, "format": "jsonv2", "limit": "1"})
        request = Request(
            f"{self.endpoint}?{params}",
            headers={"User-Agent": self.user_agent},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        if not payload:
            return GeocodeProviderResult(
                coordinate=None,
                source="nominatim",
                explanation="No geocoding result found.",
            )
        first = payload[0]
        return GeocodeProviderResult(
            coordinate=Coordinate(latitude=float(first["lat"]), longitude=float(first["lon"])),
            source="nominatim",
            explanation=first.get("display_name") or "Nominatim geocoding result.",
        )
