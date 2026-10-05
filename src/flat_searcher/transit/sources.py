"""Public open data behind the transit check, cached on disk."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from flat_searcher.transit.addresses import RegisterEntry, parse_register_rows
from flat_searcher.transit.buildings import Building, parse_buildings
from flat_searcher.transit.osm import OVERPASS_QUERY, Streets, reduce_streets, reduce_walk_graph

GTFS_PACKAGE_URL = (
    "https://data.gov.lv/dati/api/3/action/package_show?id=6d78358a-0095-4ce3-b119-6cde5d0ac54f"
)
GTFS_RESOURCE_PREFIX = "marsrutusaraksti"
REGISTER_URL = (
    "https://data.gov.lv/dati/dataset/6b06a7e8-dedf-4705-a47b-2a7c51177473/resource/"
    "a510737a-18ce-400f-ad4b-04fce5228272/download/aw_eka.csv"
)
CADASTRE_PACKAGE_URL = (
    "https://data.gov.lv/dati/api/3/action/package_show?id=be841486-4af9-4d38-aa14-6502a2ddb517"
)
BUILDINGS_RESOURCE = "building.zip"
RIGA_BUILDINGS_FILE = re.compile(r"(?:^|/)0001000_[^/]*/[^/]+\.xml$", re.IGNORECASE)
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
MAX_AGE = timedelta(days=7)
STREETS_MAX_AGE = timedelta(days=90)
USER_AGENT = "flat-searcher (+https://github.com/CHET45/flat_searcher)"

GTFS_CACHE = "rs-gtfs.zip"
REGISTER_CACHE = "riga-register.json"
BUILDINGS_CACHE = "riga-buildings.json"
STREETS_CACHE = "riga-streets.json"
WALK_CACHE = "riga-walk.json"

Opener = Callable[[str], IO[bytes]]


def open_url(url: str) -> IO[bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=300)


class TransitSourceError(RuntimeError):
    pass


class TransitSources:
    def __init__(
        self,
        cache_dir: Path,
        opener: Opener = open_url,
        now: datetime | None = None,
        *,
        refresh: bool = False,
    ) -> None:
        self._cache_dir = cache_dir
        self._open = opener
        self._now = now or datetime.now().astimezone()
        self._refresh = refresh

    def gtfs_zip(self) -> bytes:
        path = self._cache_dir / GTFS_CACHE
        if not self._fresh(path):
            with self._open(GTFS_PACKAGE_URL) as response:
                package = json.load(response)
            resources = [
                resource
                for resource in package.get("result", {}).get("resources", [])
                if str(resource.get("name", "")).casefold().startswith(GTFS_RESOURCE_PREFIX)
                and str(resource.get("name", "")).casefold().endswith(".zip")
            ]
            if not resources:
                raise TransitSourceError("the timetable dataset lists no GTFS archive")
            newest = max(resources, key=lambda resource: str(resource.get("created", "")))
            with self._open(str(newest["url"])) as response:
                _write_atomic(path, response.read())
        return path.read_bytes()

    def register(self) -> list[RegisterEntry]:
        path = self._cache_dir / REGISTER_CACHE
        if not self._fresh(path):
            with self._open(REGISTER_URL) as response:
                text = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
                buildings = parse_register_rows(csv.DictReader(text))
            _write_atomic(path, json.dumps(buildings, ensure_ascii=False).encode("utf-8"))
        return [
            RegisterEntry(str(address), float(lat), float(lon), code, bool(planned))
            for address, lat, lon, code, planned in json.loads(path.read_text(encoding="utf-8"))
        ]

    def buildings(self) -> dict[str, Building]:
        path = self._cache_dir / BUILDINGS_CACHE
        if not self._fresh(path):
            try:
                self._download_buildings(path)
            except Exception:
                if not path.exists():
                    raise
        return json.loads(path.read_text(encoding="utf-8"))

    def _download_buildings(self, path: Path) -> None:
        with self._open(CADASTRE_PACKAGE_URL) as response:
            package = json.load(response)
        urls = [
            str(resource.get("url", ""))
            for resource in package.get("result", {}).get("resources", [])
            if str(resource.get("url", "")).rsplit("/", 1)[-1] == BUILDINGS_RESOURCE
        ]
        if not urls:
            raise TransitSourceError("the cadastre dataset lists no building archive")
        archive_path = path.with_name(f".{BUILDINGS_RESOURCE}.{os.getpid()}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._open(urls[0]) as response, archive_path.open("wb") as archive_file:
                shutil.copyfileobj(response, archive_file, 1 << 20)
            with zipfile.ZipFile(archive_path) as archive:
                names = [name for name in archive.namelist() if RIGA_BUILDINGS_FILE.search(name)]
                if len(names) != 1:
                    raise TransitSourceError("the building archive has no single Riga file")
                with archive.open(names[0]) as stream:
                    buildings = parse_buildings(stream)
        finally:
            archive_path.unlink(missing_ok=True)
        if not buildings:
            raise TransitSourceError("the Riga building file lists no apartment building")
        _write_atomic(path, json.dumps(buildings, ensure_ascii=False).encode("utf-8"))

    def streets(self) -> Streets:
        self._openstreetmap()
        return json.loads((self._cache_dir / STREETS_CACHE).read_text(encoding="utf-8"))

    def walk_graph(self) -> dict[str, Any]:
        self._openstreetmap()
        return json.loads((self._cache_dir / WALK_CACHE).read_text(encoding="utf-8"))

    def _openstreetmap(self) -> None:
        paths = (self._cache_dir / STREETS_CACHE, self._cache_dir / WALK_CACHE)
        if all(self._fresh(path, STREETS_MAX_AGE) for path in paths):
            return
        failures = []
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                with self._open(f"{endpoint}?data={quote(OVERPASS_QUERY)}") as response:
                    data = json.load(response)
            except (OSError, ValueError) as error:
                failures.append(type(error).__name__)
                continue
            for path, reduced in zip(paths, (reduce_streets(data), reduce_walk_graph(data))):
                _write_atomic(path, json.dumps(reduced, ensure_ascii=False).encode("utf-8"))
            return
        if not all(path.exists() for path in paths):
            raise TransitSourceError(f"no Overpass mirror answered: {', '.join(failures)}")

    def _fresh(self, path: Path, max_age: timedelta = MAX_AGE) -> bool:
        if self._refresh or not path.exists():
            return False
        modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return self._now - modified < max_age


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
