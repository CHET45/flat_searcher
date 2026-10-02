"""Walking on the street network: a bounded Dijkstra from one point to nearby points."""

from __future__ import annotations

import math
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any

from flat_searcher.transit.geometry import (
    METRES_PER_DEGREE,
    ROAD_DETOUR,
    Point,
    distance_m,
    encode_polyline,
)

SNAP_M = 150


@dataclass(frozen=True)
class Walk:
    m: int
    points: list[Point]
    on_streets: bool

    @property
    def encoded(self) -> str:
        return encode_polyline(self.points)

    def as_dict(self) -> dict[str, Any]:
        return {"m": self.m, "line": self.encoded}


class WalkGraph:
    def __init__(self, coords: list[Point], edges: list[tuple[int, int]]) -> None:
        self._coords = coords
        degree = [0] * len(coords)
        for a, b in edges:
            degree[a] += 1
            degree[b] += 1
        self._offsets = array("i", [0] * (len(coords) + 1))
        for index, count in enumerate(degree):
            self._offsets[index + 1] = self._offsets[index] + count
        self._neighbours = array("i", [0] * (2 * len(edges)))
        self._lengths = array("f", [0.0] * (2 * len(edges)))
        cursor = array("i", self._offsets[:-1])
        for a, b in edges:
            length = distance_m(*coords[a], *coords[b])
            for source, target in ((a, b), (b, a)):
                self._neighbours[cursor[source]] = target
                self._lengths[cursor[source]] = length
                cursor[source] += 1
        self._cell_lat = SNAP_M / METRES_PER_DEGREE
        self._cell_lon = self._cell_lat / math.cos(math.radians(coords[0][0] if coords else 57))
        self._grid: dict[tuple[int, int], list[int]] = {}
        for index, (lat, lon) in enumerate(coords):
            self._grid.setdefault(self._cell(lat, lon), []).append(index)
        self._nearest: dict[Point, tuple[int, float] | None] = {}

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> WalkGraph:
        flat = list(data.get("nodes") or [])
        coords = [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat), 2)]
        pairs = list(data.get("edges") or [])
        edges = [(int(pairs[i]), int(pairs[i + 1])) for i in range(0, len(pairs), 2)]
        return cls(coords, edges)

    def nearest(self, lat: float, lon: float) -> tuple[int, float] | None:
        point = (lat, lon)
        if point in self._nearest:
            return self._nearest[point]
        row, column = self._cell(lat, lon)
        best: tuple[int, float] | None = None
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for index in self._grid.get((row + dr, column + dc), ()):
                    distance = distance_m(lat, lon, *self._coords[index])
                    if distance <= SNAP_M and (best is None or distance < best[1]):
                        best = (index, distance)
        self._nearest[point] = best
        return best

    def field(self, lat: float, lon: float, limit_m: float) -> WalkField:
        origin = self.nearest(lat, lon)
        distances: dict[int, float] = {}
        parents: dict[int, int] = {}
        if origin is not None:
            start, stub = origin
            distances[start] = 0.0
            queue = [(0.0, start)]
            while queue:
                reached, node = heappop(queue)
                if reached > distances[node]:
                    continue
                for slot in range(self._offsets[node], self._offsets[node + 1]):
                    other = self._neighbours[slot]
                    total = reached + self._lengths[slot]
                    if total <= limit_m - stub and total < distances.get(other, math.inf):
                        distances[other] = total
                        parents[other] = node
                        heappush(queue, (total, other))
        return WalkField(self, (lat, lon), origin, distances, parents)

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return int(lat // self._cell_lat), int(lon // self._cell_lon)

    def coordinates(self, index: int) -> Point:
        return self._coords[index]


class WalkField:
    def __init__(
        self,
        graph: WalkGraph,
        start: Point,
        origin: tuple[int, float] | None,
        distances: Mapping[int, float],
        parents: Mapping[int, int],
    ) -> None:
        self._graph = graph
        self._start = start
        self._origin = origin
        self._distances = distances
        self._parents = parents

    def to(self, lat: float, lon: float) -> Walk:
        end = self._graph.nearest(lat, lon)
        if self._origin is None or end is None or end[0] not in self._distances:
            straight = distance_m(*self._start, lat, lon)
            return Walk(round(straight * ROAD_DETOUR), [self._start, (lat, lon)], False)
        node = end[0]
        chain = [node]
        while node != self._origin[0]:
            node = self._parents[node]
            chain.append(node)
        points = [self._start, *(self._graph.coordinates(index) for index in reversed(chain))]
        if points[-1] != (lat, lon):
            points.append((lat, lon))
        return Walk(round(self._origin[1] + self._distances[end[0]] + end[1]), points, True)
