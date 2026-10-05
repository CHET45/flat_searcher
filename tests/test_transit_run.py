import contextlib
import io
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote

from flat_searcher.cli import main
from flat_searcher.library import LocalLibraryStore
from flat_searcher.logging_config import configure_logging
from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.transit.addresses import RegisterEntry
from flat_searcher.transit.osm import (
    OVERPASS_QUERY,
    PLACES_QUERY,
    reduce_drive_graph,
    reduce_places,
    reduce_streets,
    reduce_walk_graph,
)
from flat_searcher.transit.run import TransitRun
from flat_searcher.transit.sources import (
    CADASTRE_PACKAGE_URL,
    GTFS_PACKAGE_URL,
    OVERPASS_ENDPOINTS,
    REGISTER_URL,
    TransitSourceError,
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
    "﻿KODS,TIPS_CD,STATUSS,FOR_BUILD,STD,DD_N,DD_E\n"
    f'"1","108","EKS","N","Mājas iela 1, Rīga, LV-1001","{HOME[0]}","{HOME[1]}"\n'
    f'"2","108","EKS","N","Darba iela 9, Rīga, LV-1001","{WORK[0]}","{WORK[1]}"\n'
    '"3","108","DEL","N","Mājas iela 3, Rīga, LV-1001","56.0","24.0"\n'
    f'"4","108","EKS","Y","Jaunā iela 3, Rīga, LV-1001","{HOME[0]}","{HOME[1]}"\n'
)
OSM_RESPONSE = {
    "elements": [
        {"type": "way", "id": 1, "tags": {"highway": "primary"}, "nodes": [1, 2],
         "geometry": [{"lat": 56.9, "lon": 24.1}, {"lat": 56.9, "lon": 24.12}]},
    ]
}
STREETS_URL = OVERPASS_ENDPOINTS[0] + "?data=" + quote(OVERPASS_QUERY)
MIRROR_URL = OVERPASS_ENDPOINTS[1] + "?data=" + quote(OVERPASS_QUERY)
PLACES_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 56.9, "lon": 24.1, "tags": {"shop": "supermarket", "brand": "Rimi"}},
        {"type": "node", "id": 2, "lat": 56.91, "lon": 24.1, "tags": {"man_made": "works"}},
    ]
}
PLACES_URLS = [endpoint + "?data=" + quote(PLACES_QUERY) for endpoint in OVERPASS_ENDPOINTS]
CADASTRE_PACKAGE = {
    "result": {
        "resources": [
            {"name": "4. Zemes vienības daļu raksturojošie dati", "url": "https://x/parcelpart.zip"},
            {"name": "5. Būves raksturojošie dati", "url": "https://x/building.zip"},
        ]
    }
}


def _cadastre_file(code: str, use: str = "1122") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<BuildingFullData xmlns="http://ivis.eps.gov.lv/XMLSchemas/100007/CadastreRegistry/v1-0">'
        "<PreparedDate>2026-09-26</PreparedDate><ATVK>0001000</ATVK><BuildingItemList>"
        "<BuildingItemData><BuildingBasicData><BuildingCadastreNr>01000750713001</BuildingCadastreNr>"
        f"<VARISCode>{code}</VARISCode><BuildingUseKind><BuildingUseKindId>{use}</BuildingUseKindId>"
        "</BuildingUseKind><BuildingGroundFloors>5</BuildingGroundFloors>"
        "<BuildingExploitYear>1962</BuildingExploitYear><BuildingDeprecation>V2</BuildingDeprecation>"
        "</BuildingBasicData></BuildingItemData></BuildingItemList></BuildingFullData>"
    ).encode("utf-8")


def _cadastre_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


RIGA_FILE = "Building/0001000_20260926_540062/5C687023-DB9B.xml"
OTHER_FILE = "Building/0020000_20260926_540069/5C687023-DBA9.xml"
CADASTRE_ZIP = _cadastre_zip({RIGA_FILE: _cadastre_file("1"), OTHER_FILE: _cadastre_file("900")})
PARSED = {"wear": "V2", "wear_date": None, "built": 1962, "floors": 5, "walls": None, "use": "1122"}


class FakeOpener:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, bytes | Exception] = {
            GTFS_PACKAGE_URL: json.dumps(PACKAGE).encode("utf-8"),
            "https://x/08.zip": build_feed(),
            "https://x/07.zip": b"stale",
            REGISTER_URL: REGISTER_CSV.encode("utf-8"),
            STREETS_URL: json.dumps(OSM_RESPONSE).encode("utf-8"),
            PLACES_URLS[0]: json.dumps(PLACES_RESPONSE).encode("utf-8"),
            CADASTRE_PACKAGE_URL: json.dumps(CADASTRE_PACKAGE).encode("utf-8"),
            "https://x/building.zip": CADASTRE_ZIP,
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
            expected = [
                ("Mājas iela 1", HOME[0], HOME[1], "1", False),
                ("Darba iela 9", WORK[0], WORK[1], "2", False),
                ("Jaunā iela 3", HOME[0], HOME[1], "4", True),
            ]
            self.assertEqual(sources.register(), expected)
            self.assertEqual(sources.register(), expected)
            self.assertEqual(opener.calls, [REGISTER_URL])

    def test_buildings_come_from_the_riga_file_and_are_cached_for_a_week(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "transit"
            opener = FakeOpener()
            sources = TransitSources(cache, opener, NOW)
            self.assertEqual(sources.buildings(), {"1": PARSED})
            self.assertEqual(sources.buildings(), {"1": PARSED})
            self.assertEqual(opener.calls, [CADASTRE_PACKAGE_URL, "https://x/building.zip"])
            self.assertEqual(os.listdir(cache), ["riga-buildings.json"])

            _age(cache / "riga-buildings.json", 8)
            TransitSources(cache, opener, datetime.now(timezone.utc)).buildings()
            self.assertEqual(len(opener.calls), 4)

    def test_a_failed_building_download_keeps_the_last_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            TransitSources(Path(temp_dir), opener, NOW).buildings()
            _age(Path(temp_dir) / "riga-buildings.json", 8)
            opener.responses["https://x/building.zip"] = OSError("down")
            later = TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc))
            self.assertEqual(later.buildings(), {"1": PARSED})
            self.assertEqual(len(opener.calls), 4)

    def test_buildings_fail_without_one_riga_file_with_an_apartment_building(self) -> None:
        second_part = "Building/0001000_20260926_540199/5C687023-DCC1.xml"
        for archive in (
            _cadastre_zip({OTHER_FILE: _cadastre_file("900")}),
            _cadastre_zip({RIGA_FILE: _cadastre_file("1", use="1274")}),
            _cadastre_zip({RIGA_FILE: _cadastre_file("1"), second_part: _cadastre_file("2")}),
        ):
            with tempfile.TemporaryDirectory() as temp_dir:
                opener = FakeOpener()
                opener.responses["https://x/building.zip"] = archive
                with self.assertRaises(TransitSourceError):
                    TransitSources(Path(temp_dir), opener, NOW).buildings()
                self.assertEqual(os.listdir(temp_dir), [])

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

    def test_the_drive_graph_comes_from_the_same_download_and_its_age_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.drive_graph(), reduce_drive_graph(OSM_RESPONSE))
            self.assertEqual(sources.streets(), reduce_streets(OSM_RESPONSE))
            self.assertEqual(sources.walk_graph(), reduce_walk_graph(OSM_RESPONSE))
            self.assertEqual(opener.calls, [STREETS_URL])
            cached = json.loads((Path(temp_dir) / "riga-drive.json").read_text(encoding="utf-8"))
            self.assertEqual(cached, reduce_drive_graph(OSM_RESPONSE))

            _age(Path(temp_dir) / "riga-drive.json", 89)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).streets()
            self.assertEqual(len(opener.calls), 1)
            _age(Path(temp_dir) / "riga-drive.json", 91)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).streets()
            self.assertEqual(len(opener.calls), 2)

    def test_a_stale_drive_graph_outlives_a_day_when_every_mirror_is_down(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            TransitSources(Path(temp_dir), opener, NOW).drive_graph()
            _age(Path(temp_dir) / "riga-drive.json", 120)
            for endpoint in OVERPASS_ENDPOINTS:
                opener.responses[endpoint + "?data=" + quote(OVERPASS_QUERY)] = OSError("down")
            later = TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc))
            self.assertEqual(later.drive_graph(), reduce_drive_graph(OSM_RESPONSE))
            self.assertEqual(len(opener.calls), 4)

    def test_places_are_reduced_once_and_kept_for_a_month(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            sources = TransitSources(Path(temp_dir), opener, NOW)
            self.assertEqual(sources.places(), reduce_places(PLACES_RESPONSE))
            self.assertEqual(sources.places(), reduce_places(PLACES_RESPONSE))
            self.assertEqual(opener.calls, [PLACES_URLS[0]])
            self.assertEqual(os.listdir(temp_dir), ["riga-places.json"])

            _age(Path(temp_dir) / "riga-places.json", 29)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).places()
            self.assertEqual(len(opener.calls), 1)
            _age(Path(temp_dir) / "riga-places.json", 31)
            TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc)).places()
            self.assertEqual(len(opener.calls), 2)

    def test_places_fall_back_to_the_next_mirror_and_then_to_the_last_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            opener.responses[PLACES_URLS[0]] = OSError("down")
            opener.responses[PLACES_URLS[1]] = b"<html>busy</html>"
            opener.responses[PLACES_URLS[2]] = json.dumps(PLACES_RESPONSE).encode("utf-8")
            self.assertEqual(TransitSources(Path(temp_dir), opener, NOW).places(), reduce_places(PLACES_RESPONSE))
            self.assertEqual(opener.calls, PLACES_URLS)

            _age(Path(temp_dir) / "riga-places.json", 40)
            opener.responses[PLACES_URLS[2]] = OSError("down")
            later = TransitSources(Path(temp_dir), opener, datetime.now(timezone.utc))
            self.assertEqual(later.places(), reduce_places(PLACES_RESPONSE))
            self.assertEqual(len(opener.calls), 6)

    def test_places_fail_when_no_mirror_answers_and_nothing_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            opener = FakeOpener()
            for url in PLACES_URLS:
                opener.responses[url] = OSError("down")
            with self.assertRaises(TransitSourceError):
                TransitSources(Path(temp_dir), opener, NOW).places()
            self.assertEqual(os.listdir(temp_dir), [])


BUILDINGS = {
    "1": {"wear": "V3", "wear_date": "2019-05-14", "built": 1975, "floors": 9,
          "walls": "Saliekamā dzelzsbetona paneļi", "use": "1122"},
    "5": {"wear": "V1", "wear_date": None, "built": 2025, "floors": 6, "walls": None, "use": "1122"},
}


class FakeSources:
    def __init__(self, buildings: dict | Exception = BUILDINGS) -> None:
        self._buildings = buildings

    def gtfs_zip(self) -> bytes:
        return build_feed()

    def register(self) -> list[RegisterEntry]:
        return [
            RegisterEntry("Mājas iela 1", HOME[0], HOME[1], "1", False),
            RegisterEntry("Darba iela 9", WORK[0], WORK[1], "2", False),
            RegisterEntry("Jaunā iela 3", HOME[0], HOME[1], "3", True),
            RegisterEntry("Jaunā iela 5", HOME[0], HOME[1], "5", True),
        ]

    def buildings(self) -> dict:
        if isinstance(self._buildings, Exception):
            raise self._buildings
        return self._buildings

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


class BuildingTests(TestCase):
    def _run(self, sources: FakeSources) -> tuple[dict[str, dict], int]:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            store.save_listings(
                {
                    "a1": _listing("a1", "Mājas", "1"),
                    "b2": _listing("b2", "Mājas", "7"),
                    "e5": _listing("e5", "Jaunā", "3"),
                    "f6": _listing("f6", "Darba", "9"),
                    "g7": _listing("g7", "Jaunā", "5"),
                }
            )
            result = TransitRun(store, sources, None, NOW).run()
            return {entry["ss_id"]: entry for entry in store.read_transit()}, result.buildings

    def test_an_exact_address_carries_its_building(self) -> None:
        entries, count = self._run(FakeSources())
        self.assertEqual(entries["a1"]["building"], BUILDINGS["1"])
        self.assertEqual(entries["g7"]["building"], BUILDINGS["5"])
        self.assertEqual(count, 3)

    def test_an_approximate_address_carries_no_building(self) -> None:
        entries, _ = self._run(FakeSources())
        self.assertEqual((entries["b2"]["precision"], entries["b2"]["matched"]), ("approx", "Mājas iela 1"))
        self.assertNotIn("building", entries["b2"])

    def test_a_planned_address_without_a_registered_building_is_a_new_build(self) -> None:
        entries, _ = self._run(FakeSources())
        self.assertEqual(entries["e5"]["building"], {"note": "new build"})

    def test_an_address_without_building_data_has_no_building(self) -> None:
        entries, _ = self._run(FakeSources())
        self.assertNotIn("building", entries["f6"])

    def test_a_failing_building_source_leaves_the_run_without_building_data(self) -> None:
        entries, count = self._run(FakeSources(buildings=OSError("down")))
        self.assertEqual(sorted(entries), ["a1", "b2", "e5", "f6", "g7"])
        self.assertFalse(any("building" in entry for entry in entries.values()))
        self.assertEqual(count, 0)

    def test_the_transit_line_counts_listings_with_building_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            LocalLibraryStore(root).save_listings(
                {"a1": _listing("a1", "Mājas", "1"), "e5": _listing("e5", "Jaunā", "3")}
            )
            environment = {
                "FLAT_SEARCHER_LIBRARY_PATH": str(root),
                "FLAT_SEARCHER_ENV_FILE": str(Path(temp_dir) / "none.env"),
                "FLAT_SEARCHER_HOME": str(Path(temp_dir) / "home"),
            }
            output = io.StringIO()
            try:
                with patch.dict(os.environ, environment, clear=True), patch(
                    "flat_searcher.cli.TransitSources", lambda *args, **kwargs: FakeSources()
                ), contextlib.redirect_stdout(output):
                    code = main(["transit"])
            finally:
                configure_logging()
            self.assertEqual(code, 0)
            self.assertIn("exact=2 approx=0 unlocated=0", output.getvalue())
            self.assertIn("buildings=2", output.getvalue())


class SurroundedSources(FakeSources):
    def __init__(self, places: dict | Exception | None = None) -> None:
        super().__init__()
        self._places = places if places is not None else {"pois": [["grocery", "Rimi", *HOME]], "areas": []}

    def drive_graph(self) -> dict:
        return {"nodes": [*HOME, *WORK], "edges": [0, 1, 900, 120, 1, 0, 900, 150]}

    def places(self) -> dict:
        if isinstance(self._places, Exception):
            raise self._places
        return self._places


class SurroundingsRunTests(TestCase):
    def _run(self, sources: FakeSources) -> tuple[dict[str, dict], int]:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            listing = _listing("a1", "Mājas", "1")
            listing["core"].update({"price_eur": 40000, "declared_rooms": 2})
            dear = _listing("z9", "Mājas", "1")
            dear["core"].update({"price_eur": 90000, "declared_rooms": 2})
            store.save_listings({"a1": listing, "z9": dear, "u0": _listing("u0", "Mājas", "1")})
            result = TransitRun(store, sources, parse_criteria(CRITERIA), NOW).run()
            return {entry["ss_id"]: entry for entry in store.read_transit()}, result.surroundings

    def test_a_flat_that_passes_the_price_gate_gets_its_surroundings(self) -> None:
        entries, count = self._run(SurroundedSources())
        near = entries["a1"]["surroundings"]
        self.assertEqual(near["drive"]["targets"]["office"], {"min": 2, "km": 0.9, "back_min": 3, "back_km": 0.9})
        self.assertNotIn("lab", near["drive"]["targets"])
        self.assertEqual(near["walk"]["grocery"][0]["name"], "Rimi")
        self.assertEqual(sorted(near["on_the_way"]), ["lab", "office"])
        self.assertNotIn("surroundings", entries["z9"])
        self.assertNotIn("surroundings", entries["u0"])
        self.assertEqual(count, 1)

    def test_a_failing_source_leaves_the_run_without_surroundings(self) -> None:
        entries, count = self._run(SurroundedSources(places=OSError("down")))
        self.assertEqual(sorted(entries), ["a1", "u0", "z9"])
        self.assertFalse(any("surroundings" in entry for entry in entries.values()))
        self.assertEqual(count, 0)
