import contextlib
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.cli import main
from flat_searcher.library import LocalLibraryStore
from flat_searcher.logging_config import configure_logging
from flat_searcher.scraper.http_client import BinaryFetchResult, FetchError
from flat_searcher.transit.osm import reduce_streets
from journeys_fixture import FLAT, TARGET_ONE, build_feed, point

CRITERIA = """
[price]
max_eur = 60000
[[transit.targets]]
name = "secretplace"
address = "Darba iela 9"
"""


SHELL_ENVIRONMENT = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "COMSPEC") if key in os.environ}
GIT = shutil.which("git") or "git"


class FakeSources:
    def gtfs_zip(self) -> bytes:
        return build_feed()

    def register(self) -> list[tuple[str, float, float]]:
        return [("Mājas iela 1", *point(*FLAT)), ("Darba iela 9", *point(*TARGET_ONE))]

    def buildings(self) -> dict:
        return {}

    def streets(self) -> dict:
        return reduce_streets({"elements": []})

    def walk_graph(self) -> dict:
        return {"nodes": [], "edges": []}


def _listing(ss_id: str, price: int) -> dict:
    return {
        "ss_id": ss_id,
        "url": f"https://www.ss.com/msg/{ss_id}.html",
        "status": "active",
        "first_seen": "2026-09-01T00:00:00+00:00",
        "core": {"price_eur": price, "declared_rooms": 1, "street": "Mājas", "house_number": "1"},
        "text": {"description": ""},
        "images": [{"url": f"https://i.ss.com/gallery/{ss_id}.800.jpg", "is_floor_plan": False}],
    }


class FakeHttpClient:
    failing = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def fetch_bytes(self, url: str) -> BinaryFetchResult:
        if self.failing:
            raise FetchError(url)
        return BinaryFetchResult(url=url, content=b"jpeg")


class FailingHttpClient(FakeHttpClient):
    failing = True


class ShortlistCliTests(TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "private-library-location"
        self.store = LocalLibraryStore(self.root)
        self.store.save_listings({"a1": _listing("a1", 35000), "b2": _listing("b2", 95000)})
        environment = {
            "FLAT_SEARCHER_LIBRARY_PATH": str(self.root),
            "FLAT_SEARCHER_ENV_FILE": str(Path(self._temp.name) / "none.env"),
            "FLAT_SEARCHER_HOME": str(Path(self._temp.name) / "home"),
        }
        self._environment = patch.dict(os.environ, environment, clear=True)
        self._environment.start()

    def tearDown(self) -> None:
        configure_logging()
        self._environment.stop()
        self._temp.cleanup()

    def _run(self, *argv: str, http: type = FakeHttpClient) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch(
            "flat_searcher.cli.TransitSources", lambda *args, **kwargs: FakeSources()
        ), patch("flat_searcher.cli.HttpTextClient", http):
            code = main(list(argv))
        text = output.getvalue()
        self.assertNotIn("private-library-location", text)
        return code, text

    def _write_criteria(self, text: str) -> None:
        (self.root / "criteria.toml").write_text(text, encoding="utf-8")

    def test_digest_without_criteria_writes_nothing(self) -> None:
        code, text = self._run("digest", "--day", "2026-09-24")
        self.assertEqual(code, 0)
        self.assertIn("criteria.toml", text)
        self.assertEqual(self.store.digest_days(), [])

    def _digest(self, http: type = FakeHttpClient) -> tuple[int, str]:
        self._write_criteria(CRITERIA)
        self._run("transit")
        return self._run("digest", "--day", "2026-09-24", http=http)

    def test_digest_prints_counts_and_writes_the_page(self) -> None:
        code, text = self._digest()
        self.assertEqual(code, 0)
        self.assertIn("candidates=1", text)
        self.assertIn("flats=1", text)
        self.assertIn("rejected_price=1", text)
        self.assertNotIn("secretplace", text)
        self.assertEqual(self.store.digest_days(), ["2026-09-24"])

    def test_digest_writes_the_page_its_map_data_and_photos_as_files(self) -> None:
        code, text = self._digest()
        self.assertEqual(code, 0)
        self.assertIn("photos=1", text)
        site = self.root / "digest" / "2026-09-24"
        self.assertIn('"photos/a1.jpg"', (site / "index.html").read_text(encoding="utf-8"))
        self.assertNotIn("data:image", (site / "index.html").read_text(encoding="utf-8"))
        self.assertEqual((site / "photos" / "a1.jpg").read_bytes(), b"jpeg")
        self.assertTrue((site / "map.js").read_text(encoding="utf-8").startswith("window.flatMapData = "))
        self.assertEqual(sorted(path.name for path in (site / "photos").iterdir()), ["a1.jpg"])

    def test_digest_page_is_written_even_when_no_photo_loads(self) -> None:
        code, text = self._digest(http=FailingHttpClient)
        self.assertEqual(code, 0)
        self.assertIn("photos=0", text)
        self.assertTrue((self.root / "digest" / "2026-09-24" / "index.html").exists())

    def test_digest_refuses_targets_the_last_transit_run_did_not_compute(self) -> None:
        self._write_criteria(CRITERIA)
        code, text = self._run("digest", "--day", "2026-09-24")
        self.assertEqual(code, 1)
        self.assertIn("run transit", text)
        self.assertEqual(self.store.digest_days(), [])
        self._run("transit")
        self._write_criteria(CRITERIA.replace("secretplace", "renamedplace"))
        code, text = self._run("digest", "--day", "2026-09-24")
        self.assertEqual(code, 1)
        self.assertIn("targets_changed=1", text)
        self.assertNotIn("renamedplace", text)
        self.assertEqual(self.store.digest_days(), [])

    def test_invalid_criteria_names_the_key(self) -> None:
        self._write_criteria("[price]\nmax_eur = 1\nmax_price = 2\n")
        code, text = self._run("digest", "--day", "2026-09-24")
        self.assertEqual(code, 1)
        self.assertIn("max_price", text)

    def _publish(self, **environment: str) -> tuple[int, str]:
        self._digest()
        with patch.dict(os.environ, {**SHELL_ENVIRONMENT, **environment}):
            return self._run("publish", "--day", "2026-09-24")

    def test_publish_pushes_the_page_and_prints_counts_only(self) -> None:
        bare = Path(self._temp.name) / "secret-site.git"
        subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=SHELL_ENVIRONMENT)
        code, text = self._publish(FLAT_SEARCHER_PAGES_REPO=str(bare))
        self.assertEqual(code, 0)
        self.assertIn("published day=2026-09-24 files=3 bytes=", text)
        self.assertNotIn("secret-site", text)
        shown = subprocess.run(
            [GIT, "--git-dir", str(bare), "show", "main:index.html"],
            capture_output=True, text=True, encoding="utf-8", check=True, env=SHELL_ENVIRONMENT,
        ).stdout
        self.assertIn("<title>Riga Flat Shortlist</title>", shown)

    def test_publish_without_configuration_does_nothing(self) -> None:
        code, text = self._publish()
        self.assertEqual(code, 0)
        self.assertIn("FLAT_SEARCHER_PAGES_REPO", text)

    def test_publish_failure_names_the_error_type_only(self) -> None:
        code, text = self._publish(FLAT_SEARCHER_PAGES_REPO=str(Path(self._temp.name) / "secret-nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("Publish failed: PublishError", text)
        self.assertNotIn("secret-nowhere", text)

    def test_transit_prints_counts_but_never_target_names(self) -> None:
        self._write_criteria(CRITERIA)
        code, text = self._run("transit")
        self.assertEqual(code, 0)
        self.assertIn("exact=2", text)
        self.assertIn("reached_any=2", text)
        self.assertNotIn("secretplace", text)
        self.assertNotIn("Home stop", text)
        self.assertEqual(len(self.store.read_transit()), 2)
        self.assertIn("shapes", self.store.read_transit_map())
