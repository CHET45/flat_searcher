"""Riga's streets, railways and water from OpenStreetMap: a drawing and a walking graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from flat_searcher.transit.geometry import Point, encode_polyline, simplify

RIGA_BBOX = "(56.85,23.9,57.1,24.35)"
ROAD_TIERS = (
    ("motorway", "trunk", "primary"),
    ("secondary", "tertiary"),
    ("residential", "unclassified", "living_street", "pedestrian"),
)
WALK_EXCLUDED = frozenset(
    {
        "motorway", "motorway_link", "trunk", "trunk_link", "proposed", "construction",
        "raceway", "bus_guideway", "busway", "abandoned", "planned",
    }
)
FOOT_ALLOWED = frozenset({"yes", "designated", "permissive"})
ROAD_TOLERANCE_M = 3
WATER_TOLERANCE_M = 5
OVERPASS_QUERY = (
    "[out:json][timeout:300];("
    f'way["highway"]{RIGA_BBOX};'
    f'way["railway"="rail"]{RIGA_BBOX};'
    f'way["natural"="water"]{RIGA_BBOX};'
    f'relation["natural"="water"]{RIGA_BBOX};'
    ");out geom;"
)

Streets = dict[str, Any]


def reduce_streets(response: Mapping[str, Any]) -> Streets:
    roads: list[list[str]] = [[] for _ in ROAD_TIERS]
    rail: list[str] = []
    water: list[tuple[str, str]] = []
    for element in response.get("elements", []):
        tags = element.get("tags") or {}
        if element.get("type") == "relation":
            if tags.get("natural") == "water":
                water += _relation_rings(element.get("members") or [])
            continue
        points = _points(element.get("geometry") or [])
        if len(points) < 2:
            continue
        tier = _tier(tags.get("highway", ""))
        if tier is not None:
            roads[tier].append(encode_polyline(simplify(points, ROAD_TOLERANCE_M)))
        elif tags.get("railway") == "rail" and not tags.get("service"):
            rail.append(encode_polyline(simplify(points, ROAD_TOLERANCE_M)))
        elif tags.get("natural") == "water" and points[0] == points[-1]:
            water.append(("outer", encode_polyline(simplify(points, WATER_TOLERANCE_M))))
    return {"roads": roads, "rail": rail, "water": water}


def reduce_walk_graph(response: Mapping[str, Any]) -> dict[str, list[float] | list[int]]:
    index: dict[int, int] = {}
    coords: list[float] = []
    edges: list[int] = []
    seen: set[tuple[int, int]] = set()
    for element in response.get("elements", []):
        tags = element.get("tags") or {}
        if element.get("type") != "way" or not _walkable(tags):
            continue
        nodes = element.get("nodes") or []
        geometry = element.get("geometry") or []
        if len(nodes) != len(geometry) or len(nodes) < 2:
            continue
        ids = []
        for node_id, node in zip(nodes, geometry):
            if node_id not in index:
                index[node_id] = len(index)
                coords += [round(float(node["lat"]), 6), round(float(node["lon"]), 6)]
            ids.append(index[node_id])
        for a, b in zip(ids, ids[1:]):
            if a != b and (a, b) not in seen and (b, a) not in seen:
                seen.add((a, b))
                edges += [a, b]
    return {"nodes": coords, "edges": edges}


def _walkable(tags: Mapping[str, str]) -> bool:
    highway = tags.get("highway")
    if not highway or highway in WALK_EXCLUDED or tags.get("foot") == "no":
        return False
    if tags.get("access") in ("private", "no") and tags.get("foot") not in FOOT_ALLOWED:
        return False
    return True


def _tier(highway: str) -> int | None:
    kind = highway.removesuffix("_link")
    for index, kinds in enumerate(ROAD_TIERS):
        if kind in kinds:
            return index
    return None


def _points(geometry: Sequence[Mapping[str, float]]) -> list[Point]:
    return [(float(node["lat"]), float(node["lon"])) for node in geometry]


def _relation_rings(members: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    rings: list[tuple[str, str]] = []
    for role in ("outer", "inner"):
        pieces = [
            _points(member.get("geometry") or [])
            for member in members
            if member.get("role") == role and member.get("type") == "way"
        ]
        for ring in _assemble(pieces):
            rings.append((role, encode_polyline(simplify(ring, WATER_TOLERANCE_M))))
    return rings


def _assemble(pieces: list[list[Point]]) -> list[list[Point]]:
    open_pieces = [piece for piece in pieces if len(piece) >= 2]
    rings: list[list[Point]] = []
    while open_pieces:
        ring = open_pieces.pop()
        while ring[0] != ring[-1]:
            for index, piece in enumerate(open_pieces):
                if piece[0] == ring[-1]:
                    ring += piece[1:]
                elif piece[-1] == ring[-1]:
                    ring += piece[-2::-1]
                else:
                    continue
                del open_pieces[index]
                break
            else:
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings
