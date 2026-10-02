import io
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from urllib.parse import quote

from flat_searcher.library import LocalLibraryStore
from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.transit.osm import OVERPASS_QUERY, reduce_streets, reduce_walk_graph
from flat_searcher.transit.run import TransitRun
from flat_searcher.transit.sources import (
    GTFS_PACKAGE_URL,
    OVERPASS_ENDPOINTS,
    REGISTER_URL,
    TransitSources,
)
from journeys_fixture import FLAT, TARGET_ONE, TARGET_TWO, build_feed, point

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
PACKAGE = {
    "result": {
        "resources": [
            {"name": "MarsrutuSaraksti07_2026.zip", "created": "2026-08-21T10:23:35", "url": "https://x/07.zip"},
            {"name": "MarsrutuSaraksti08_2026.zip", "created": "2026-09-08T07:00:08", "url": "https://x/08.zip"},
            {"name": "Readme.csv", "created": "2026-09-20T00:00:00", "url": "https://x/readme.csv"},
        ]
    }
}
HOME = point(*FLAT)
WORK = point(*TARGET_ONE)
REGISTER_CSV = (
    "﻿KODS,TIPS_CD,STATUSS,STD,DD_N,DD_E\n"
    f'"1","108","EKS","Mājas iela 1, Rīga, LV-1001","{HOME[0]}","{HOME[1]}"\n'
    f'"2","108","EKS","Darba iela 9, Rīga, LV-1001","{WORK[0]}","{WORK[1]}"\n'
    '"3","108","DEL","Mājas iela 3, Rīga, LV-1001","56.0","24.0"\n'
)
OSM_RESPONSE = {
    "elements": [
        {"type": "way", "id": 1, "tags": {"highway": "primary"}, "nodes": [1, 2],
         "geometry": [{"lat": 56.9, "lon": 24.1}, {"lat": 56.9, "lon": 24.12}]},
    ]
}
STREETS_URL = OVERPASS_ENDPOINTS[0] + "?data=" + quote(OVERPASS_QUERY)
MIRROR_URL = OVERPASS_ENDPOINTS[1] + "?data=" + quote(OVERPASS_QUERY)


class FakeOpener:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, bytes | Exception] = {
            GTFS_PACKAGE_URL: json.dumps(PACKAGE).encode("utf-8"),
            "https://x/08.zip": build_feed(),
            "https://x/07.zip": b"stale",
            REGISTER_URL: REGISTER_CSV.encode("utf-8"),
            STREETS_URL: json.dumps(OSM_RESPONSE).encode("utf-8"),
        }

    def __call__(self, url: str) -> io.BytesIO:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


def _age(path: Path, days: int) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


class TransitSourcesTests(TestCase):
    def test_newest_timetable_is_chosen_and_cached_for_a_week(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.gtfs_zip(), build_feed())
            self.assertEqual(opener.calls, [GTFS_PACKAGE_URL, "https://x/08.zip"])
            sources.gtfs_zip()
            self.assertEqual(len(opener.calls), 2)

            _age(Path(temp_dir) / "rs-gtfs.zip", 8)
            later = TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc))
            later.gtfs_zip()
            self.assertEqual(len(opener.calls), 4)

    def test_refresh_ignores_a_fresh_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            TransitSources(Path(temp_dir), opener, NOW).gtfs_zip()
            TransitSources(Path(temp_dir), opener, NOW, refresh=True).gtfs_zip()
            self.assertEqual(len(opener.calls), 4)

    def test_register_keeps_the_riga_subset_and_caches_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            expected = [("Mājas iela 1", HOME[0], HOME[1]), ("Darba iela 9", WORK[0], WORK[1])]
            self.assertEqual(sources.register(), expected)
            self.assertEqual(sources.register(), expected)
            self.assertEqual(opener.calls, [REGISTER_URL])

    def test_streets_are_reduced_once_and_kept_for_three_months(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(sources.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(opener.calls, [STREETS_URL])
            cached = json.loads((Path(temp_dir) / "riga-streets.json").read_text(encoding="utf-8"))
            self.assertEqual(cached, reduce_streets(OSM_RESPONSE))

            _age(Path(temp_dir) / "riga-streets.json", 89)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).streets()
            self.assertEqual(len(opener.calls), 1)
            _age(Path(temp_dir) / "riga-streets.json", 91)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).streets()
            self.assertEqual(len(opener.calls), 2)

    def test_a_stale_streets_cache_outlives_a_day_when_every_mirror_is_down(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            TransitSources(Path(temp_dir), opener, NOW).streets()
            _age(Path(temp_dir) / "riga-streets.json", 120)
            for url in (STREETS_URL, MIRROR_URL, OVERPASS_ENDPOINTS[2] + "?data=" + quote(OVERPASS_QUERY)):
                opener.responses[url] = OSError("down")
            later = TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc))
            self.assertEqual(later.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(len(opener.calls), 4)

    def test_the_walk_graph_comes_from_the_same_download_as_the_streets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.walk_graph(), reduce_walk_graph(OSM_RESPONSE))
            self.assertEqual(sources.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(opener.calls, [STREETS_URL])
            self.assertTrue((Path(temp_dir) / "riga-walk.json").exists())

    def test_streets_fall_back_to_the_next_overpass_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            opener.responses[STREETS_URL] = OSError("down")
            opener.responses[MIRROR_URL] = json.dumps(OSM_RESPONSE).encode("utf-8")
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(opener.calls, [STREETS_URL, MIRROR_URL])


class FakeSources:
    def gtfs_zip(self) -> bytes:
        return build_feed()

    def register(self) -> list[tuple[str, float, float]]:
        return [("Mājas iela 1", HOME[0], HOME[1]), ("Darba iela 9", WORK[0], WORK[1])]

    def streets(self) -> dict:
        return reduce_streets(OSM_RESPONSE)

    def walk_graph(self) -> dict:
        return reduce_walk_graph(OSM_RESPONSE)


def _listing(ss_id: str, street: str, house: str | None, status: str = "active") -> dict:
    return {"ss_id": ss_id, "status": status, "core": {"street": street, "house_number": house}}


CRITERIA = f"""
[price]
max_eur = 60000
[transit]
walk_m = 500
[[transit.targets]]
name = "office"
address = "Darba iela 9"
[[transit.targets]]
name = "lab"
lat = {point(*TARGET_TWO)[0]}
lon = {point(*TARGET_TWO)[1]}
[[transit.targets]]
name = "lost"
address = "Neesošā iela 4"
"""


class TransitRunTests(TestCase):
    def _store(self, root: str) -> LocalLibraryStore:
        store = LocalLibraryStore(Path(root))
        store.save_listings(
            {
                "a1": _listing("a1", "Mājas", "1"),
                "b2": _listing("b2", "Mājas", "7"),
                "c3": _listing("c3", "Nekur", "2"),
                "d4": _listing("d4", "Mājas", "1", status="removed"),
            }
        )
        return store

    def test_journeys_per_target_are_written_for_located_listings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            result = TransitRun(store, FakeSources(), parse_criteria(CRITERIA), NOW).run()
            entries = {entry["ss_id"]: entry for entry in store.read_transit()}

            self.assertEqual(sorted(entries), ["a1", "b2"])
            self.assertEqual(entries["a1"]["precision"], "exact")
            self.assertEqual(entries["b2"]["precision"], "approx")
            office = entries["a1"]["targets"]["office"]
            self.assertEqual([[leg["routes"] for leg in journey["legs"]] for journey in office],
                             [[["bus 1", "bus 5"]], [["bus 8"]]])
            self.assertEqual((office[0]["minutes"], office[0]["every_min"], office[0]["transfers"]), (13, 8, 0))
            self.assertEqual([sorted(walk) for walk in office[0]["walks"]], [["line", "m"], ["line", "m"]])
            lab = entries["a1"]["targets"]["lab"]
            self.assertEqual([leg["board"] for leg in lab[0]["legs"]], ["h1", "hub_b"])
            self.assertEqual((entries["a1"]["day"], entries["a1"]["window"]), ("2026-09-29", "07:00-10:00"))
            self.assertEqual(entries["a1"]["feed"], "20260901-20270901")
            self.assertEqual(
                (result.exact, result.approx, result.unlocated, result.targets,
                 result.unresolved_targets, result.reached_any),
                (1, 1, 1, 2, 1, 2),
            )

    def test_the_map_file_carries_shapes_stops_and_streets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            TransitRun(store, FakeSources(), parse_criteria(CRITERIA), NOW).run()
            data = store.read_transit_map()
            self.assertEqual(sorted(data["shapes"]), ["bus_1_shape", "bus_3_shape", "tram_2_shape"])
            self.assertEqual(sorted(data["stops"]), ["h1", "h8", "hub_a", "hub_b", "t1", "t2"])
            self.assertEqual(data["stops"]["h1"], [*point(0, 0), "Home stop"])
            self.assertEqual(data["streets"], reduce_streets(OSM_RESPONSE))
            lab = point(*TARGET_TWO)
            self.assertEqual(
                store.read_transit_targets(),
                {"office": {"lat": WORK[0], "lon": WORK[1],
                            "defined_as": {"address": "Darba iela 9", "lat": None, "lon": None}},
                 "lab": {"lat": lab[0], "lon": lab[1],
                         "defined_as": {"address": None, "lat": lab[0], "lon": lab[1]}}},
            )

    def test_without_criteria_listings_are_located_but_no_target_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            result = TransitRun(store, FakeSources(), None, NOW).run()
            entries = store.read_transit()
            self.assertEqual([entry["targets"] for entry in entries], [{}, {}])
            self.assertEqual((result.targets, result.reached_any), (0, 0))
            self.assertEqual(store.read_transit_map()["streets"], reduce_streets(OSM_RESPONSE))
