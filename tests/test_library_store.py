import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.library import (
    MIN_MARKET_SAMPLE,
    UNKNOWN,
    GitLibraryError,
    LibraryError,
    GitLibraryStore,
    LocalLibraryStore,
    classify_change,
    content_hash,
    diff_records,
    district_market_stats,
    merge_judgment,
)
from flat_searcher.library import store as store_module


def _record(ss_id: str, price: int = 100_000, visits: int = 10, district: str = "Centrs") -> dict:
    return {
        "ss_id": ss_id,
        "url": f"https://www.ss.com/msg/lv/{ss_id}.html",
        "status": "active",
        "first_seen": "2026-09-01T00:00:00+00:00",
        "last_seen": "2026-09-01T00:00:00+00:00",
        "content_hash": "",
        "revision": 1,
        "core": {"price_eur": price, "area_m2": 50, "district": district},
        "fields": {"Cena": f"{price} €", "Sērija": "103."},
        "text": {"title": f"Flat {ss_id}", "description": "Sunny."},
        "images": [{"url": f"https://i.ss.com/gallery/{ss_id}.jpg", "is_floor_plan": False}],
        "stats": {"unique_visits": visits},
    }


class LocalLibraryStoreTests(TestCase):
    def test_save_then_load_is_identical_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            listings = {"b2": _record("b2"), "a1": _record("a1"), "c3": _record("c3")}

            store.save_listings(listings)
            loaded = store.load_listings()

            self.assertEqual(loaded, listings)
            lines = (Path(temp_dir) / "listings.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line)["ss_id"] for line in lines], ["a1", "b2", "c3"])

    def test_shuffled_input_produces_a_byte_identical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = LocalLibraryStore(Path(temp_dir) / "one")
            second = LocalLibraryStore(Path(temp_dir) / "two")
            records = [_record("a1"), _record("b2"), _record("c3")]

            first.save_listings({record["ss_id"]: record for record in records})
            second.save_listings({record["ss_id"]: record for record in reversed(records)})

            self.assertEqual(
                (first.root / "listings.jsonl").read_bytes(),
                (second.root / "listings.jsonl").read_bytes(),
            )

    def test_failure_mid_write_leaves_the_previous_file_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            store.save_listings({"a1": _record("a1")})
            before = (store.root / "listings.jsonl").read_bytes()

            def explode(*args, **kwargs):
                raise OSError("disk full")

            with patch.object(store_module.os, "replace", explode):
                with self.assertRaises(OSError):
                    store.save_listings({"a1": _record("a1"), "b2": _record("b2")})

            self.assertEqual((store.root / "listings.jsonl").read_bytes(), before)
            self.assertEqual([path.name for path in store.root.glob(".*.tmp")], [])

    def test_events_append_and_queue_verdicts_meta_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)

            store.append_events([{"at": "t1", "ss_id": "a1", "event": "listing_added"}])
            store.append_events([{"at": "t2", "ss_id": "a1", "event": "listing_changed"}])
            store.write_queue("2026-09-14", [{"ss_id": "a1", "reason": "new"}])
            store.write_meta({"status": "ok", "counts": {"new": 1}})
            (store.root / "verdicts").mkdir()
            (store.root / "verdicts" / "2026-09-14.jsonl").write_text(
                '{"ss_id": "a1", "score": 71}\n', encoding="utf-8"
            )

            events = (store.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 2)
            queue = (store.root / "queue" / "2026-09-14.jsonl").read_text(encoding="utf-8")
            self.assertEqual(json.loads(queue)["reason"], "new")
            self.assertEqual(store.load_meta()["counts"]["new"], 1)
            self.assertEqual(store.verdict_days(), ["2026-09-14"])
            self.assertEqual(store.read_verdicts("2026-09-14"), [{"ss_id": "a1", "score": 71}])
            self.assertEqual(store.read_verdicts("2026-01-01"), [])

    def test_unicode_line_separators_stay_inside_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            record = _record("a1")
            record["text"]["description"] = "First paragraph second third\x85fourth"

            store.save_listings({"a1": record})

            raw = (store.root / "listings.jsonl").read_text(encoding="utf-8")
            self.assertEqual(len(raw.splitlines()), 1)
            self.assertEqual(raw.count("\n"), 1)
            self.assertEqual(store.load_listings()["a1"], record)

    def test_missing_library_loads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir) / "never-created")

            self.assertEqual(store.load_listings(), {})
            self.assertEqual(store.load_meta(), {})
            self.assertEqual(store.verdict_days(), [])


class RecordLogicTests(TestCase):
    def test_visits_change_keeps_the_content_hash_and_price_change_breaks_it(self) -> None:
        base = _record("a1", price=100_000, visits=10)

        self.assertEqual(content_hash(base), content_hash(_record("a1", price=100_000, visits=99)))
        self.assertNotEqual(content_hash(base), content_hash(_record("a1", price=95_000)))

    def test_diff_and_change_classification(self) -> None:
        before = _record("a1", price=100_000, visits=10)

        visits_only = diff_records(before, _record("a1", price=100_000, visits=11))
        price_change = diff_records(before, _record("a1", price=95_000, visits=11))

        self.assertEqual(visits_only, {"stats.unique_visits": [10, 11]})
        self.assertEqual(classify_change(visits_only), "incidental")
        self.assertEqual(price_change["core.price_eur"], [100_000, 95_000])
        self.assertEqual(price_change["fields.Cena"], ["100000 €", "95000 €"])
        self.assertEqual(classify_change(price_change), "substantive")

    def test_district_median_needs_a_minimum_sample(self) -> None:
        thin = [_record(f"t{index}", price=100_000 + index * 1_000) for index in range(4)]
        enough = [
            _record(f"e{index}", price=100_000 + index * 1_000, district="Teika")
            for index in range(MIN_MARKET_SAMPLE)
        ]

        stats = district_market_stats(thin + enough)

        self.assertEqual(MIN_MARKET_SAMPLE, 5)
        self.assertEqual(stats["Centrs"], {"median_price_per_m2": UNKNOWN, "sample_size": 4})
        self.assertEqual(stats["Teika"]["sample_size"], 5)
        self.assertEqual(stats["Teika"]["median_price_per_m2"], 2040.0)

    def test_merge_judgment_keeps_envelope_out_of_the_record(self) -> None:
        merged = merge_judgment(
            _record("a1"),
            {"ss_id": "a1", "judged_at": "2026-09-14T10:00:00Z", "score": 71, "verdict": "ok"},
        )

        self.assertEqual(merged["judgment"]["score"], 71)
        self.assertEqual(merged["judgment"]["judged_at"], "2026-09-14T10:00:00Z")
        self.assertNotIn("ss_id", merged["judgment"])


class GitLibraryStoreTests(TestCase):
    def _bare_remote(self, root: Path) -> str:
        remote = root / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
            check=True,
            capture_output=True,
        )
        return remote.as_uri()

    def test_push_lands_in_the_remote_and_leaks_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote = self._bare_remote(root)
            token = "ghp_super_secret_token"
            store = GitLibraryStore(remote, token, root / "work")

            store.pull()
            store.save_listings({"a1": _record("a1")})
            self.assertTrue(store.push("Index 2026-09-14"))
            self.assertFalse(store.push("Index again"))

            clone = root / "clone"
            subprocess.run(
                ["git", "clone", "-q", remote, str(clone)], check=True, capture_output=True
            )
            self.assertTrue((clone / "library" / "listings.jsonl").exists())
            log = subprocess.run(
                ["git", "-C", str(clone), "log", "--all", "--format=%s %b"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn(token, log)
            config = (root / "work" / ".git" / "config").read_text(encoding="utf-8")
            self.assertNotIn(token, config)

    def test_second_pull_sees_what_the_first_pushed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote = self._bare_remote(root)

            first = GitLibraryStore(remote, "", root / "one")
            first.pull()
            first.save_listings({"a1": _record("a1")})
            first.push()

            second = GitLibraryStore(remote, "", root / "two")
            second.pull()

            self.assertEqual(list(second.load_listings()), ["a1"])

    def test_push_failure_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token = "ghp_super_secret_token"
            store = GitLibraryStore(
                "https://example.invalid/private/library.git", token, root / "work"
            )
            store.save_listings({"a1": _record("a1")})
            subprocess.run(
                ["git", "-C", str(root / "work"), "init", "-q", "-b", "main"],
                check=True,
                capture_output=True,
            )

            with self.assertRaises(GitLibraryError) as caught:
                store.push()

            message = str(caught.exception)
            self.assertNotIn(token, message)
            self.assertNotIn("example.invalid", message)


class ShortlistStorageTests(TestCase):
    def test_criteria_is_absent_until_written_and_loses_its_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            self.assertIsNone(store.read_criteria())
            (Path(temp_dir) / "criteria.toml").write_bytes("﻿[price]\n".encode("utf-8"))
            self.assertEqual(store.read_criteria(), "[price]\n")

    def test_transit_roundtrip_is_sorted_by_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            self.assertEqual(store.read_transit(), [])
            store.write_transit([{"ss_id": "b2", "targets": {}}, {"ss_id": "a1", "targets": {}}])
            self.assertEqual([entry["ss_id"] for entry in store.read_transit()], ["a1", "b2"])

    def test_events_read_back_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            store.append_events([{"ss_id": "b"}, {"ss_id": "a"}])
            self.assertEqual([event["ss_id"] for event in store.read_events()], ["b", "a"])

    def test_digest_days_list_only_markdown_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            store.write_digest("2026-09-25", "# b\n")
            store.write_digest("2026-09-24", "# a\n")
            (Path(temp_dir) / "digest" / "notes.txt").write_text("x", encoding="utf-8")
            self.assertEqual(store.digest_days(), ["2026-09-24", "2026-09-25"])
            written = (Path(temp_dir) / "digest" / "2026-09-24.md").read_bytes()
            self.assertEqual(written, b"# a\n")

    def test_git_store_delegates_shortlist_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = GitLibraryStore("https://example.invalid/r.git", "", Path(temp_dir))
            store.write_transit([{"ss_id": "a1"}])
            store.write_digest("2026-09-24", "# a\n")
            store.append_events([{"ss_id": "a1"}])
            self.assertEqual(store.read_transit(), [{"ss_id": "a1"}])
            self.assertEqual(store.digest_days(), ["2026-09-24"])
            self.assertEqual(store.read_events(), [{"ss_id": "a1"}])
            self.assertIsNone(store.read_criteria())

    def test_digest_site_is_a_folder_beside_the_markdown_replaced_whole(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            store.write_digest("2026-09-24", "# a\n")
            self.assertIsNone(store.digest_site("2026-09-24"))
            store.write_digest_site("2026-09-24", {"index.html": "<title>x</title>\n",
                                                   "photos/a.jpg": b"\xff\xd8", "photos/b.jpg": b"b"})
            store.write_digest_site("2026-09-24", {"index.html": "<title>y</title>\n", "photos/a.jpg": b"a"})
            site = store.digest_site("2026-09-24")
            assert site is not None
            self.assertEqual(site, Path(temp_dir) / "digest" / "2026-09-24")
            self.assertEqual((site / "index.html").read_bytes(), b"<title>y</title>\n")
            self.assertEqual(sorted(path.name for path in (site / "photos").iterdir()), ["a.jpg"])
            self.assertEqual(sorted(path.name for path in site.parent.iterdir()), ["2026-09-24", "2026-09-24.md"])
            self.assertEqual(store.digest_days(), ["2026-09-24"])

    def test_digest_site_paths_stay_inside_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            with self.assertRaises(LibraryError):
                store.write_digest_site("2026-09-24", {"../escape.html": "x"})
            self.assertFalse((Path(temp_dir) / "digest" / "escape.html").exists())
            self.assertIsNone(store.digest_site("2026-09-24"))

    def test_transit_map_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            self.assertEqual(store.read_transit_map(), {})
            data = {"shapes": {"s": "abc"}, "stops": {"x": [56.9, 24.1]}, "streets": {"roads": [[], [], []]}}
            store.write_transit_map(data)
            self.assertEqual(store.read_transit_map(), data)

    def test_transit_targets_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(Path(temp_dir))
            self.assertEqual(store.read_transit_targets(), {})
            store.write_transit_targets({"office": {"lat": 56.9, "lon": 24.1}})
            self.assertEqual(store.read_transit_targets(), {"office": {"lat": 56.9, "lon": 24.1}})
            git_store = GitLibraryStore("https://example.invalid/r.git", "", Path(temp_dir) / "g")
            git_store.write_transit_targets({"a": {"lat": 1.0, "lon": 2.0}})
            git_store.write_digest_site("2026-09-24", {"index.html": "p"})
            self.assertIsNotNone(git_store.digest_site("2026-09-24"))
            self.assertEqual(git_store.read_transit_targets(), {"a": {"lat": 1.0, "lon": 2.0}})
