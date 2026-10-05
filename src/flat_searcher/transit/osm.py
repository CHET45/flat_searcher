"""Riga from OpenStreetMap: a street drawing, walking and driving graphs, shops and land use."""

from __future__ import annotations

import re
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from flat_searcher.transit.geometry import (
    METRES_PER_DEGREE,
    Point,
    distance_m,
    encode_polyline,
    simplify,
)

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
DRIVE_SPEEDS_KMH = {
    "motorway": 70, "trunk": 55, "primary": 40, "secondary": 35, "tertiary": 30,
    "unclassified": 25, "residential": 20, "living_street": 10, "service": 12,
}
LINKED_ROADS = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"})
LINK_FACTOR = 0.8
MAXSPEED_FACTOR = 0.8
SERVICE_EXCLUDED = frozenset({"parking_aisle", "drive-through", "emergency_access"})
MOTOR_ACCESS_KEYS = ("motorcar", "motor_vehicle", "access")
ACCESS_DENIED = frozenset({"no", "private", "psv", "bus"})
ONEWAY_FORWARD = frozenset({"yes", "true", "1"})
ROUNDABOUTS = frozenset({"roundabout", "circular"})
IMPLIED_ONEWAY = frozenset({"motorway", "motorway_link"})
GROCERY_SHOPS = frozenset({"supermarket", "convenience", "grocery"})
DIY_SHOPS = frozenset({"doityourself"})
FUEL_AND_KIOSK_BRANDS = frozenset(
    {"circle k", "neste", "virši", "viada", "gotika", "narvesen", "plus punkts"}
)
AREA_TAGS = (
    ("industrial", "landuse", "industrial"),
    ("works", "man_made", "works"),
    ("landfill", "landuse", "landfill"),
    ("wastewater", "man_made", "wastewater_plant"),
    ("cemetery", "landuse", "cemetery"),
    ("cemetery", "amenity", "grave_yard"),
    ("bog", "natural", "wetland"),
    ("railway", "landuse", "railway"),
)
WETLAND_EXCLUDED = frozenset({"reedbed", "tidalflat", "saltmarsh"})
INDUSTRIAL_MIN_HA = 2
AREA_TOLERANCE_M = 10
PLACES_QUERY = (
    "[out:json][timeout:300];("
    f'nwr["shop"~"^(supermarket|convenience|grocery|doityourself|mall)$"]{RIGA_BBOX};'
    f'nwr["leisure"="fitness_centre"]{RIGA_BBOX};'
    f'nwr["leisure"="sports_centre"]["sport"~"fitness"]{RIGA_BBOX};'
    f'nwr["landuse"~"^(industrial|landfill|cemetery|railway)$"]{RIGA_BBOX};'
    f'nwr["man_made"~"^(works|wastewater_plant)$"]{RIGA_BBOX};'
    f'nwr["amenity"="grave_yard"]{RIGA_BBOX};'
    f'nwr["natural"="wetland"]{RIGA_BBOX};'
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
        ids = [_graph_node(index, coords, node_id, node) for node_id, node in zip(nodes, geometry)]
        for a, b in zip(ids, ids[1:]):
            if a != b and (a, b) not in seen and (b, a) not in seen:
                seen.add((a, b))
                edges += [a, b]
    return {"nodes": coords, "edges": edges}


def reduce_drive_graph(response: Mapping[str, Any]) -> dict[str, list[float] | list[int]]:
    ways: list[tuple[list[int], list[Mapping[str, float]], float, tuple[bool, bool]]] = []
    uses: Counter[int] = Counter()
    for element in response.get("elements", []):
        tags = element.get("tags") or {}
        speed = _drive_speed_kmh(tags) if element.get("type") == "way" else None
        nodes = element.get("nodes") or []
        geometry = element.get("geometry") or []
        if speed is None or len(nodes) != len(geometry) or len(nodes) < 2:
            continue
        ways.append((nodes, geometry, speed / 3.6, _directions(tags)))
        uses.update(nodes)
    index: dict[int, int] = {}
    coords: list[float] = []
    fastest: dict[tuple[int, int], tuple[float, float]] = {}
    for nodes, geometry, speed, (forward, backward) in ways:
        start, metres = 0, 0.0
        for position in range(1, len(nodes)):
            before, here = geometry[position - 1], geometry[position]
            metres += distance_m(before["lat"], before["lon"], here["lat"], here["lon"])
            if position < len(nodes) - 1 and uses[nodes[position]] == 1:
                continue
            if nodes[start] != nodes[position]:
                a = _graph_node(index, coords, nodes[start], geometry[start])
                b = _graph_node(index, coords, nodes[position], here)
                seconds = metres / speed
                for edge in ([(a, b)] if forward else []) + ([(b, a)] if backward else []):
                    if edge not in fastest or seconds < fastest[edge][1]:
                        fastest[edge] = (metres, seconds)
            start, metres = position, 0.0
    edges: list[int] = []
    for (a, b), (metres, seconds) in fastest.items():
        edges += [a, b, round(metres), max(1, round(seconds))]
    return {"nodes": coords, "edges": edges}


def reduce_places(response: Mapping[str, Any]) -> dict[str, list[list[Any]]]:
    pois: list[list[Any]] = []
    areas: list[list[Any]] = []
    for element in response.get("elements", []):
        tags = element.get("tags") or {}
        categories, kinds = _categories(tags), _area_kinds(tags)
        outline = _outline(element) if categories or kinds else []
        if not outline:
            continue
        name = tags.get("brand") or tags.get("name") or ""
        lats, lons = [lat for lat, _ in outline], [lon for _, lon in outline]
        centre = [round((min(lats) + max(lats)) / 2, 6), round((min(lons) + max(lons)) / 2, 6)]
        pois += [[category, name, *centre] for category in categories]
        if element.get("type") == "node":
            rings = [outline]
        elif element.get("type") == "way":
            rings = _assemble([outline])
        else:
            rings = _assemble(_member_points(element, "outer"))
        hectares = [_hectares(ring) for ring in rings]
        for kind in kinds:
            if kind == "industrial" and sum(hectares) < INDUSTRIAL_MIN_HA:
                continue
            areas += [
                [kind, name, encode_polyline(simplify(ring, AREA_TOLERANCE_M)), round(size, 2)]
                for ring, size in zip(rings, hectares)
            ]
    return {"pois": pois, "areas": areas}


def _drive_speed_kmh(tags: Mapping[str, str]) -> float | None:
    highway = tags.get("highway", "")
    road = highway.removesuffix("_link")
    if road not in DRIVE_SPEEDS_KMH or (road != highway and road not in LINKED_ROADS):
        return None
    if tags.get("service") in SERVICE_EXCLUDED:
        return None
    if next((tags[key] for key in MOTOR_ACCESS_KEYS if key in tags), None) in ACCESS_DENIED:
        return None
    speed = DRIVE_SPEEDS_KMH[road] * (LINK_FACTOR if road != highway else 1)
    maxspeed = tags.get("maxspeed", "")
    if maxspeed.isdecimal() and int(maxspeed) > 0:
        speed = min(speed, MAXSPEED_FACTOR * int(maxspeed))
    return speed


def _directions(tags: Mapping[str, str]) -> tuple[bool, bool]:
    oneway = tags.get("oneway")
    if oneway == "-1":
        return False, True
    if oneway in ONEWAY_FORWARD or (
        oneway != "no"
        and (tags.get("junction") in ROUNDABOUTS or tags.get("highway") in IMPLIED_ONEWAY)
    ):
        return True, False
    return True, True


def _graph_node(index: dict[int, int], coords: list[float], node_id: int, node: Mapping[str, float]) -> int:
    if node_id not in index:
        index[node_id] = len(index)
        coords += [round(float(node["lat"]), 6), round(float(node["lon"]), 6)]
    return index[node_id]


def _categories(tags: Mapping[str, str]) -> list[str]:
    shop, leisure = tags.get("shop"), tags.get("leisure")
    sports = {sport.strip() for sport in tags.get("sport", "").split(";")}
    found = []
    if shop in GROCERY_SHOPS and not _fuel_or_kiosk(tags):
        found.append("grocery")
    if leisure == "fitness_centre" or (leisure == "sports_centre" and "fitness" in sports):
        found.append("gym")
    if shop in DIY_SHOPS:
        found.append("diy")
    if shop == "mall":
        found.append("mall")
    return found


def _fuel_or_kiosk(tags: Mapping[str, str]) -> bool:
    if tags.get("amenity") == "fuel":
        return True
    words = (re.split(r"[\s-]+", tags.get(key, "").casefold()) for key in ("brand", "name"))
    return any(" ".join(name[:size]) in FUEL_AND_KIOSK_BRANDS for name in words for size in (1, 2))


def _area_kinds(tags: Mapping[str, str]) -> list[str]:
    kinds = dict.fromkeys(kind for kind, key, value in AREA_TAGS if tags.get(key) == value)
    if tags.get("wetland") in WETLAND_EXCLUDED:
        kinds.pop("bog", None)
    return list(kinds)


def _outline(element: Mapping[str, Any]) -> list[Point]:
    if element.get("type") == "node":
        return [(float(element["lat"]), float(element["lon"]))]
    if element.get("type") == "way":
        return _points(element.get("geometry") or [])
    return [point for piece in _member_points(element, None) for point in piece]


def _member_points(element: Mapping[str, Any], role: str | None) -> list[list[Point]]:
    return [
        _points(member.get("geometry") or [])
        for member in element.get("members") or []
        if role in (None, member.get("role"))
    ]


def _hectares(ring: Sequence[Point]) -> float:
    lat0, lon0 = ring[0]
    scale = math.cos(math.radians(lat0)) * METRES_PER_DEGREE
    xs = [(lon - lon0) * scale for _, lon in ring]
    ys = [(lat - lat0) * METRES_PER_DEGREE for lat, _ in ring]
    twice = sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(ring) - 1))
    return abs(twice) / 2 / 10_000


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
