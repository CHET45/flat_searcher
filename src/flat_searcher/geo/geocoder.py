"""Geocoding provider abstractions."""

from __future__ import annotations

import json
import time
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
        user_agent: str = "flat-searcher/0.1 (Riga apartment analyzer)",
        endpoint: str = "https://nominatim.openstreetmap.org/search",
        timeout_seconds: float = 30.0,
        request_delay_seconds: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.request_delay_seconds = request_delay_seconds
        self._last_request_at = 0.0

    def geocode(self, query: str) -> GeocodeProviderResult:
        self._wait_if_needed()
        params = urlencode({"q": query, "format": "jsonv2", "limit": "1"})
        request = Request(
            f"{self.endpoint}?{params}",
            headers={"User-Agent": self.user_agent},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        finally:
            self._last_request_at = time.monotonic()
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

    def _wait_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_seconds = self.request_delay_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
