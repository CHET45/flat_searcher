"""Locate every active listing and record its journeys to each target."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from flat_searcher.library import ACTIVE, LibraryStore
from flat_searcher.shortlist.criteria import Criteria, target_definition
from flat_searcher.shortlist.rank import expected_minutes
from flat_searcher.transit.addresses import APPROX, EXACT, AddressIndex, Location, RegisterEntry
from flat_searcher.transit.geometry import encode_polyline
from flat_searcher.transit.gtfs import TransitFeed
from flat_searcher.transit.driving import DriveGraph
from flat_searcher.transit.journeys import Planner, Target
from flat_searcher.transit.osm import Streets
from flat_searcher.transit.surroundings import Place, Places, TargetFields, surroundings, target_fields
from flat_searcher.transit.walking import WalkGraph

NEW_BUILD = "new build"

logger = logging.getLogger(__name__)

Buildings = Mapping[str, Mapping[str, Any]]


class Sources(Protocol):
    def gtfs_zip(self) -> bytes: ...

    def register(self) -> list[RegisterEntry]: ...

    def buildings(self) -> Buildings: ...

    def drive_graph(self) -> dict[str, Any]: ...

    def places(self) -> dict[str, Any]: ...

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
    surroundings: int = 0


class TransitRun:
    def __init__(
        self, store: LibraryStore, sources: Sources, criteria: Criteria | None, now: datetime
    ) -> None:
        self._store = store
        self._sources = sources
        self._criteria = criteria
        self._now = now

    def run(self, progress: Callable[[int, int], None] | None = None) -> TransitResult:
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

        feed = planner = walk = None
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
                walk=(walk := WalkGraph.from_data(self._sources.walk_graph())),
                transfer_walk_m=self._criteria.transfer_walk_m,
            )
            targets = {name: planner.target(lat, lon) for name, (lat, lon) in points.items()}

        buildings = self._buildings()
        around = self._around(points) if walk is not None else None
        gyms = (around[1].by_category.get("gym") or []) if around is not None else []
        gym_target = planner.nearest([(gym.lat, gym.lon) for gym in gyms]) if planner and gyms else None
        near_at: dict[tuple[float, float], dict[str, Any]] = {}
        computed_at = self._now.isoformat(timespec="seconds")
        counts = {EXACT: 0, APPROX: 0}
        unlocated = reached_any = with_building = with_surroundings = 0
        entries: list[dict[str, Any]] = []
        shapes_used: set[str] = set()
        stops_used: set[str] = set()
        options_at: dict[tuple[float, float], dict[str, list[dict[str, Any]]]] = {}
        active = [
            (ss_id, record)
            for ss_id, record in self._store.load_listings().items()
            if record.get("status") == ACTIVE
        ]
        for done, (ss_id, record) in enumerate(active, start=1):
            if progress is not None:
                progress(done, len(active))
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
            if around is not None and walk is not None and self._worth_surroundings(core):
                point = (location.lat, location.lon)
                if point not in near_at:
                    near_at[point] = surroundings(point, walk, *around, _first_walks(journeys))
                    if planner is not None:
                        near_at[point]["transit"] = {"gym": _by_transit(planner, gym_target, gyms, point)}
                entry["surroundings"] = near_at[point]
                with_surroundings += 1
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
            surroundings=with_surroundings,
        )

    def _around(
        self, points: Mapping[str, tuple[float, float]]
    ) -> tuple[DriveGraph, Places, dict[str, TargetFields]] | None:
        try:
            drive = DriveGraph.from_data(self._sources.drive_graph())
            places = Places(self._sources.places())
        except Exception as error:
            logger.warning("surroundings unavailable: %s", type(error).__name__)
            return None
        return drive, places, {name: target_fields(drive, lat, lon) for name, (lat, lon) in points.items()}

    def _worth_surroundings(self, core: Mapping[str, Any]) -> bool:
        """Only flats that pass the price and rooms gates: the rest never reach the shortlist."""
        if self._criteria is None:
            return False
        price, rooms = core.get("price_eur"), core.get("declared_rooms")
        if not isinstance(price, (int, float)) or self._criteria.band_index(price) is None:
            return False
        order = self._criteria.room_order
        return not order or rooms is None or rooms in order

    def _buildings(self) -> Buildings | None:
        try:
            return self._sources.buildings()
        except Exception as error:
            logger.warning("building register unavailable: %s", type(error).__name__)
            return None


def _first_walks(journeys: Mapping[str, list[dict[str, Any]]]) -> dict[str, str]:
    first: dict[str, str] = {}
    for name, options in journeys.items():
        best = min(options, key=expected_minutes, default=None)
        walks = (best or {}).get("walks") or []
        if walks and walks[0].get("line"):
            first[name] = walks[0]["line"]
    return first


def _by_transit(
    planner: Planner, target: Target | None, places: Sequence[Place], point: tuple[float, float]
) -> list[dict[str, Any]]:
    if target is None or not (options := planner.options(target, *point)):
        return []
    best = options[0]
    return [
        {
            **places[target.point_of(best)].as_dict(),
            "min": best.minutes,
            "every_min": best.every_min,
            "transfers": best.transfers,
            "walk_m": best.walk_m,
            "routes": [list(leg.routes) for leg in best.legs],
        }
    ]


def _building(location: Location, buildings: Buildings | None) -> Mapping[str, Any] | None:
    if buildings is None or location.precision != EXACT:
        return None
    found = buildings.get(location.address_code) if location.address_code else None
    if found is None and location.planned:
        return {"note": NEW_BUILD}
    return found
