"""Locate every active listing and record its journeys to each target."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Any, Protocol

from flat_searcher.library import ACTIVE, LibraryStore
from flat_searcher.shortlist.criteria import Criteria, target_definition
from flat_searcher.transit.addresses import APPROX, EXACT, AddressIndex, Location, RegisterEntry
from flat_searcher.transit.geometry import encode_polyline
from flat_searcher.transit.gtfs import TransitFeed
from flat_searcher.transit.journeys import Planner, Target
from flat_searcher.transit.osm import Streets
from flat_searcher.transit.walking import WalkGraph

NEW_BUILD = "new build"

logger = logging.getLogger(__name__)

Buildings = Mapping[str, Mapping[str, Any]]


class Sources(Protocol):
    def gtfs_zip(self) -> bytes: ...

    def register(self) -> list[RegisterEntry]: ...

    def buildings(self) -> Buildings: ...

    def streets(self) -> Streets: ...

    def walk_graph(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class TransitResult:
    exact: int
    approx: int
    unlocated: int
    targets: int
    unresolved_targets: int
    reached_any: int
    buildings: int


class TransitRun:
    def __init__(
        self, store: LibraryStore, sources: Sources, criteria: Criteria | None, now: datetime
    ) -> None:
        self._store = store
        self._sources = sources
        self._criteria = criteria
        self._now = now

    def run(self) -> TransitResult:
        index = AddressIndex(self._sources.register())
        points: dict[str, tuple[float, float]] = {}
        definitions: dict[str, dict[str, Any]] = {}
        unresolved = 0
        for target in self._criteria.targets if self._criteria else ():
            definitions[target.name] = target_definition(target)
            if target.lat is not None and target.lon is not None:
                points[target.name] = (target.lat, target.lon)
                continue
            location = index.locate_text(target.address or "")
            if location is None:
                unresolved += 1
            else:
                points[target.name] = (location.lat, location.lon)

        feed = planner = None
        targets: dict[str, Target] = {}
        if points and self._criteria:
            feed = TransitFeed.from_zip(
                self._sources.gtfs_zip(), self._now.date(), self._criteria.transfer_walk_m
            )
            planner = Planner(
                feed,
                self._criteria.window,
                self._criteria.walk_m,
                self._criteria.max_transfers,
                self._criteria.journeys_max,
                walk=WalkGraph.from_data(self._sources.walk_graph()),
                transfer_walk_m=self._criteria.transfer_walk_m,
            )
            targets = {name: planner.target(lat, lon) for name, (lat, lon) in points.items()}

        buildings = self._buildings()
        computed_at = self._now.isoformat(timespec="seconds")
        counts = {EXACT: 0, APPROX: 0}
        unlocated = reached_any = with_building = 0
        entries: list[dict[str, Any]] = []
        shapes_used: set[str] = set()
        stops_used: set[str] = set()
        options_at: dict[tuple[float, float], dict[str, list[dict[str, Any]]]] = {}
        for ss_id, record in self._store.load_listings().items():
            if record.get("status") != ACTIVE:
                continue
            core = record.get("core") or {}
            location = index.locate(str(core.get("street") or ""), core.get("house_number"))
            if location is None:
                unlocated += 1
                continue
            counts[location.precision] += 1
            journeys: dict[str, list[dict[str, Any]]] = {}
            if planner is not None:
                point = (location.lat, location.lon)
                if point not in options_at:
                    options_at[point] = {}
                    for name, target in targets.items():
                        options = planner.options(target, *point)
                        options_at[point][name] = [option.as_dict() for option in options]
                        for option in options:
                            for leg in option.legs:
                                stops_used.update((leg.board, leg.alight))
                                if leg.shape:
                                    shapes_used.add(leg.shape)
                journeys = options_at[point]
            if any(journeys.values()):
                reached_any += 1
            entry: dict[str, Any] = {
                "ss_id": ss_id,
                "precision": location.precision,
                "matched": location.matched,
                "lat": location.lat,
                "lon": location.lon,
                "targets": journeys,
                "feed": feed.label if feed else None,
                "day": feed.day.isoformat() if feed else None,
                "window": "-".join(self._criteria.window) if self._criteria else None,
                "computed_at": computed_at,
            }
            building = _building(location, buildings)
            if building is not None:
                entry["building"] = building
                with_building += 1
            entries.append(entry)
        self._store.write_transit(entries)
        self._store.write_transit_targets(
            {
                name: {"lat": lat, "lon": lon, "defined_as": definitions[name]}
                for name, (lat, lon) in points.items()
            }
        )
        self._store.write_transit_map(
            {
                "shapes": {
                    shape: encode_polyline(feed.shapes[shape]) for shape in sorted(shapes_used)
                }
                if feed
                else {},
                "stops": {
                    stop: [*feed.stops[stop], feed.stop_names[stop]] for stop in sorted(stops_used)
                }
                if feed
                else {},
                "streets": self._sources.streets(),
            }
        )
        return TransitResult(
            exact=counts[EXACT],
            approx=counts[APPROX],
            unlocated=unlocated,
            targets=len(points),
            unresolved_targets=unresolved,
            reached_any=reached_any,
            buildings=with_building,
        )

    def _buildings(self) -> Buildings | None:
        try:
            return self._sources.buildings()
        except Exception as error:
            logger.warning("building register unavailable: %s", type(error).__name__)
            return None


def _building(location: Location, buildings: Buildings | None) -> Mapping[str, Any] | None:
    if buildings is None or location.precision != EXACT:
        return None
    found = buildings.get(location.address_code) if location.address_code else None
    if found is None and location.planned:
        return {"note": NEW_BUILD}
    return found
