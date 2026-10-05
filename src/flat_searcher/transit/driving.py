"""Driving on the street network: one-way aware travel times from or to one point."""

from __future__ import annotations

import math
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any

from flat_searcher.transit.geometry import METRES_PER_DEGREE, ROAD_DETOUR, Point, distance_m

SNAP_M = 400
ACCESS_KMH = 15

Edge = tuple[int, int, float, float]
Adjacency = tuple[array[int], array[int], array[float], array[float]]


@dataclass(frozen=True)
class Leg:
    seconds: int
    metres: int

    @property
    def minutes(self) -> int:
        return max(1, (self.seconds + 30) // 60)

    @property
    def km(self) -> float:
        return (self.metres + 50) // 100 / 10


class DriveGraph:
    def __init__(self, coords: list[Point], edges: Sequence[Edge]) -> None:
        self._coords = coords
        self._forward = _adjacency(len(coords), edges, reverse=False)
        self._reverse = _adjacency(len(coords), edges, reverse=True)
        self._cell_lat = SNAP_M / METRES_PER_DEGREE
        self._cell_lon = self._cell_lat / math.cos(math.radians(coords[0][0] if coords else 57))
        self._grid: dict[tuple[int, int], list[int]] = {}
        for index in _largest_component(len(coords), self._forward, self._reverse):
            self._grid.setdefault(self._cell(*coords[index]), []).append(index)
        self._nearest: dict[Point, tuple[int, float] | None] = {}

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> DriveGraph:
        flat = list(data.get("nodes") or [])
        coords = [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat), 2)]
        rows = list(data.get("edges") or [])
        edges = [
            (int(rows[i]), int(rows[i + 1]), float(rows[i + 2]), float(rows[i + 3]))
            for i in range(0, len(rows), 4)
        ]
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

    def field(
        self, lat: float, lon: float, limit_s: float | None = None, *, reverse: bool = False
    ) -> DriveField:
        origin = self.nearest(lat, lon)
        if origin is None:
            return DriveField(self, None)
        start, stub = origin
        start_s, start_m = _access(stub)
        limit = math.inf if limit_s is None else limit_s
        offsets, targets, edge_s, edge_m = self._reverse if reverse else self._forward
        best = [math.inf] * len(self._coords)
        length = [0.0] * len(self._coords)
        queue: list[tuple[float, int]] = []
        if start_s <= limit:
            best[start] = start_s
            length[start] = start_m
            queue.append((start_s, start))
        pop, push = heappop, heappush
        while queue:
            reached, node = pop(queue)
            if reached > best[node]:
                continue
            here_m = length[node]
            for slot in range(offsets[node], offsets[node + 1]):
                total = reached + edge_s[slot]
                other = targets[slot]
                if total < best[other] and total <= limit:
                    best[other] = total
                    length[other] = here_m + edge_m[slot]
                    push(queue, (total, other))
        return DriveField(self, (array("d", best), array("d", length)))

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return int(lat // self._cell_lat), int(lon // self._cell_lon)


class DriveField:
    def __init__(
        self, graph: DriveGraph, costs: tuple[array[float], array[float]] | None
    ) -> None:
        self._graph = graph
        self._costs = costs

    def node_cost(self, node: int) -> tuple[float, float] | None:
        if self._costs is None or self._costs[0][node] == math.inf:
            return None
        return self._costs[0][node], self._costs[1][node]

    def at(self, lat: float, lon: float) -> Leg | None:
        end = self._graph.nearest(lat, lon)
        cost = None if end is None else self.node_cost(end[0])
        if end is None or cost is None:
            return None
        access_s, access_m = _access(end[1])
        return Leg(round(cost[0] + access_s), round(cost[1] + access_m))


def _access(straight_m: float) -> tuple[float, float]:
    metres = straight_m * ROAD_DETOUR
    return metres * 3.6 / ACCESS_KMH, metres


def _adjacency(count: int, edges: Sequence[Edge], *, reverse: bool) -> Adjacency:
    offsets = array("i", bytes(4 * (count + 1)))
    for source, target, _, _ in edges:
        offsets[(target if reverse else source) + 1] += 1
    for index in range(count):
        offsets[index + 1] += offsets[index]
    targets = array("i", bytes(4 * len(edges)))
    seconds = array("d", bytes(8 * len(edges)))
    metres = array("d", bytes(8 * len(edges)))
    cursor = offsets[:-1]
    for source, target, length, duration in edges:
        if reverse:
            source, target = target, source
        slot = cursor[source]
        targets[slot], seconds[slot], metres[slot] = target, duration, length
        cursor[source] += 1
    return offsets, targets, seconds, metres


def _largest_component(count: int, forward: Adjacency, reverse: Adjacency) -> list[int]:
    offsets, targets = forward[0], forward[1]
    seen = bytearray(count)
    order: list[int] = []
    for root in range(count):
        if seen[root]:
            continue
        seen[root] = 1
        stack = [(root, offsets[root])]
        while stack:
            node, slot = stack[-1]
            if slot < offsets[node + 1]:
                stack[-1] = (node, slot + 1)
                other = targets[slot]
                if not seen[other]:
                    seen[other] = 1
                    stack.append((other, offsets[other]))
            else:
                stack.pop()
                order.append(node)
    offsets, targets = reverse[0], reverse[1]
    assigned = bytearray(count)
    largest: list[int] = []
    for root in reversed(order):
        if assigned[root]:
            continue
        assigned[root] = 1
        members = [root]
        for node in members:
            for slot in range(offsets[node], offsets[node + 1]):
                other = targets[slot]
                if not assigned[other]:
                    assigned[other] = 1
                    members.append(other)
        if len(members) > len(largest):
            largest = members
    return largest
