import json
import tempfile
from unittest import TestCase
from unittest.mock import patch

from flat_searcher.indexing import IndexerOptions, IndexerRun, merge_verdicts
from flat_searcher.library import LocalLibraryStore
from flat_searcher.scraper.http_client import FetchError, FetchResult

START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"
PAGE_2_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html"


class FakeHttpClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def fetch_text(self, url: str) -> FetchResult:
        if url not in self.pages:
            raise FetchError(f"Missing page: {url}")
        return FetchResult(url=url, text=self.pages[url])


def _site(
    listings: dict[str, dict],
    *,
    drop_page_2: bool = False,
) -> dict[str, str]:
    ids = sorted(listings)
    first, rest = ids[:2], ids[2:]
    pages = {
        START_URL: _list_page(1, first, listings, next_path="/lv/real-estate/flats/riga/all/sell/page2.html" if rest else None),
    }
    if rest and not drop_page_2:
        pages[PAGE_2_URL] = _list_page(2, rest, listings)
    for ss_id, spec in listings.items():
        pages[f"https://www.ss.com/msg/lv/{ss_id}.html"] = _detail_page(spec)
    return pages


def _list_page(page: int, ids: list[str], listings: dict[str, dict], next_path: str | None = None) -> str:
    rows = "".join(
        f"""
        <tr id="tr_{ss_id}">
          <td></td>
          <td><a href="/msg/lv/{ss_id}.html"></a></td>
          <td><a class="am" href="/msg/lv/{ss_id}.html">Apartment {ss_id}</a></td>
          <td>Centrs<br>Testa {ss_id}</td>
          <td>2</td>
          <td>50</td>
          <td>2/5</td>
          <td>Renov.</td>
          <td>{listings[ss_id]["price"]} €</td>
        </tr>
        """
        for ss_id in ids
    )
    next_link = (
        f'<a name="nav_id" rel="next" class="navi" href="{next_path}">Nakamie</a>'
        if next_path
        else ""
    )
    return (
        f"<html><body><table>{rows}</table>"
        f'<div><button class="navia">{page}</button>{next_link}</div></body></html>'
    )


def _detail_page(spec: dict) -> str:
    images = "".join(
        f'<a href="https://i.ss.com/gallery/{name}.jpg">img</a>' for name in spec.get("images", [])
    )
    return (
        f'<html><body><div id="msg_div_msg">{spec.get("description", "Nice flat.")}'
        f'<table class="options_list"><tr><td class="ads_opt_name">Cena:</td>'
        f'<td class="ads_opt">{spec["price"]} € ({spec["price"] // 50} €/m²)</td></tr>'
        f'<tr><td class="ads_opt_name">Platība:</td><td class="ads_opt">50 m²</td></tr>'
        f"</table></div>{images}"
        f'<div>Unikālo apmeklējumu skaits: {spec.get("visits", 1)}</div></body></html>'
    )


def _run(store: LocalLibraryStore, pages: dict[str, str], **options) -> object:
    return IndexerRun(
        store,
        IndexerOptions(start_url=START_URL, request_delay_seconds=0.0, **options),
        FakeHttpClient(pages),
    ).run()


def _queue(store: LocalLibraryStore) -> list[dict]:
    files = sorted(store.root.glob("queue/*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[-1].read_text(encoding="utf-8").splitlines()]


def _counts(result, stage: str) -> dict[str, int]:
    return next(outcome.counts for outcome in result.stages if outcome.name == stage)


class IndexerRunTests(TestCase):
    def test_first_run_indexes_every_field_and_queues_everything(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            listings = {"1001": {"price": 100_000, "images": ["a"]}, "1002": {"price": 90_000}}

            result = _run(store, _site(listings))

            self.assertTrue(result.ok)
            self.assertEqual(_counts(result, "promote"), {"new": 2, "changed": 0, "unchanged": 0, "reactivated": 0, "removed": 0})
            library = store.load_listings()
            self.assertEqual(sorted(library), ["1001", "1002"])
            record = library["1001"]
            self.assertEqual(record["core"]["price_eur"], 100_000)
            self.assertEqual(record["fields"]["Cena"], "100000 € (2000 €/m²)")
            self.assertEqual(record["fields"]["Platība"], "50 m²")
            self.assertEqual(record["images"], [{"url": "https://i.ss.com/gallery/a.jpg", "is_floor_plan": False}])
            self.assertEqual(record["market"]["price_per_m2"], 2000.0)
            self.assertEqual({entry["ss_id"]: entry["reason"] for entry in _queue(store)}, {"1001": "new", "1002": "new"})
            self.assertEqual(store.load_meta()["status"], "ok")
            self.assertTrue(store.load_meta()["crawl_complete"])

    def test_identical_second_run_advances_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            pages = _site({"1001": {"price": 100_000}, "1002": {"price": 90_000}})
            _run(store, pages)
            before = (store.root / "listings.jsonl").read_bytes()
            first_queue = store.root / "queue"
            first_files = sorted(first_queue.glob("*.jsonl"))
            for path in first_files:
                path.unlink()

            result = _run(store, pages)

            self.assertEqual(_counts(result, "promote")["unchanged"], 2)
            self.assertEqual(_counts(result, "write")["queued"], 0)
            self.assertEqual(_counts(result, "write")["events"], 0)
            self.assertEqual(sorted(first_queue.glob("*.jsonl")), [])
            after = json.loads((store.root / "listings.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(json.loads(before.decode("utf-8").splitlines()[0])["content_hash"], after["content_hash"])
            self.assertEqual(after["revision"], 1)

    def test_same_day_rerun_keeps_earlier_unjudged_queue_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            pages = _site({"1001": {"price": 100_000}, "1002": {"price": 90_000}})
            _run(store, pages)
            _judge(store, "1002")

            result = _run(store, pages)

            self.assertEqual(_counts(result, "promote")["unchanged"], 2)
            self.assertEqual(_counts(result, "write")["carried"], 1)
            queue = _queue(store)
            self.assertEqual([entry["ss_id"] for entry in queue], ["1001"])
            self.assertEqual(queue[0]["reason"], "new")

    def test_visits_only_change_updates_the_library_but_stays_out_of_the_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            _run(store, _site({"1001": {"price": 100_000, "visits": 5}}))
            _judge(store, "1001")
            for path in store.root.glob("queue/*.jsonl"):
                path.unlink()

            result = _run(store, _site({"1001": {"price": 100_000, "visits": 42}}))

            self.assertEqual(_counts(result, "promote")["changed"], 1)
            self.assertEqual(store.load_listings()["1001"]["stats"]["unique_visits"], 42)
            self.assertEqual(_queue(store), [])
            events = [json.loads(line) for line in (store.root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["event"], "listing_changed")
            self.assertEqual(events[-1]["changed"], {"stats.unique_visits": [5, 42]})

    def test_price_change_enters_the_queue_with_the_changed_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            _run(store, _site({"1001": {"price": 100_000}}))
            _judge(store, "1001")

            result = _run(store, _site({"1001": {"price": 95_000}}))

            self.assertEqual(_counts(result, "write")["queued"], 1)
            entry = _queue(store)[0]
            self.assertEqual(entry["reason"], "changed")
            self.assertEqual(entry["changed"]["core.price_eur"], [100_000, 95_000])
            self.assertEqual(entry["record"]["core"]["price_eur"], 95_000)
            self.assertEqual(entry["record"]["revision"], 2)

    def test_verdict_from_an_earlier_day_is_merged_and_stops_requeueing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            pages = _site({"1001": {"price": 100_000}})
            _run(store, pages)
            _judge(store, "1001", score=71)

            result = _run(store, pages)

            self.assertEqual(_counts(result, "verdicts")["merged"], 1)
            judgment = store.load_listings()["1001"]["judgment"]
            self.assertEqual(judgment["score"], 71)
            self.assertNotIn("ss_id", judgment)
            self.assertEqual(_counts(result, "write")["queued"], 0)

    def test_unjudged_listing_is_requeued_until_a_verdict_arrives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            pages = _site({"1001": {"price": 100_000, "visits": 1}})
            _run(store, pages)
            for path in store.root.glob("queue/*.jsonl"):
                path.unlink()

            result = _run(store, _site({"1001": {"price": 100_000, "visits": 2}}))

            self.assertEqual(_counts(result, "write")["queued"], 1)
            self.assertEqual(_queue(store)[0]["reason"], "never_judged")

    def test_complete_crawl_removes_missing_listings_but_incomplete_crawl_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            three = {"1001": {"price": 1}, "1002": {"price": 2}, "1003": {"price": 3}}
            _run(store, _site(three))

            incomplete = _run(store, _site(three, drop_page_2=True))
            self.assertTrue(incomplete.ok)
            self.assertFalse(incomplete.meta["crawl_complete"])
            self.assertTrue(incomplete.meta["deactivation_skipped"])
            self.assertEqual(_counts(incomplete, "promote")["removed"], 0)
            self.assertEqual(store.load_listings()["1003"]["status"], "active")

            complete = _run(store, _site({"1001": {"price": 1}, "1002": {"price": 2}}))
            self.assertEqual(_counts(complete, "promote")["removed"], 1)
            self.assertEqual(store.load_listings()["1003"]["status"], "removed")
            self.assertEqual(store.load_listings()["1003"]["revision"], 2)

    def test_removed_listing_that_returns_is_reactivated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            _run(store, _site({"1001": {"price": 1}, "1002": {"price": 2}}))
            _run(store, _site({"1001": {"price": 1}}))
            self.assertEqual(store.load_listings()["1002"]["status"], "removed")

            result = _run(store, _site({"1001": {"price": 1}, "1002": {"price": 2}}))

            self.assertEqual(_counts(result, "promote"), {"new": 0, "changed": 0, "unchanged": 1, "reactivated": 1, "removed": 0})
            self.assertEqual(store.load_listings()["1002"]["status"], "active")

    def test_failing_stage_is_reported_and_later_stages_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)

            result = _run(store, {})

            self.assertFalse(result.ok)
            statuses = {outcome.name: outcome.status for outcome in result.stages}
            self.assertEqual(statuses["crawl"], "failed")
            self.assertEqual(statuses["write"], "skipped")
            meta = store.load_meta()
            self.assertEqual(meta["status"], "failed")
            failed = next(stage for stage in meta["stages"] if stage["name"] == "crawl")
            self.assertEqual(failed["error"], "FetchError")
            self.assertNotIn("ss.com", json.dumps(meta))
            self.assertFalse((store.root / "listings.jsonl").exists())

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)

            result = _run(store, _site({"1001": {"price": 100_000}}), dry_run=True)

            self.assertTrue(result.ok)
            self.assertEqual(_counts(result, "write")["queued"], 1)
            self.assertEqual(sorted(path.name for path in store.root.iterdir()), [])

    def test_market_median_needs_five_district_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            listings = {str(1000 + index): {"price": 100_000 + index * 5_000} for index in range(5)}

            _run(store, _site(listings))

            market = store.load_listings()["1002"]["market"]
            self.assertEqual(market["district_sample_size"], 5)
            self.assertEqual(market["district_median_price_per_m2"], 2200.0)

    def test_stage_error_text_never_reaches_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            with patch.object(store, "load_listings", side_effect=RuntimeError("https://secret.host/lib")):
                with self.assertLogs("flat_searcher.indexing.run", level="ERROR") as logs:
                    result = IndexerRun(
                        store, IndexerOptions(start_url=START_URL), FakeHttpClient({})
                    ).run()

            self.assertFalse(result.ok)
            self.assertNotIn("secret.host", "\n".join(logs.output))
            self.assertNotIn("secret.host", json.dumps(result.meta))


class Halt(BaseException):
    pass


class IndexerProgressTests(TestCase):
    def test_crawl_progress_reaches_the_caller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            events: list[tuple[str, int, int]] = []
            IndexerRun(
                store,
                IndexerOptions(start_url=START_URL, request_delay_seconds=0.0),
                FakeHttpClient(_site({"1001": {"price": 100_000}, "1002": {"price": 90_000}})),
            ).run(progress=lambda phase, done, total: events.append((phase, done, total)))
            self.assertEqual(events[-2:], [("listings", 1, 2), ("listings", 2, 2)])

    def test_a_stop_raised_from_progress_leaves_the_library_and_meta_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            pages = _site({"1001": {"price": 100_000}})
            _run(store, pages)
            meta = store.load_meta()

            def halt(phase: str, done: int, total: int) -> None:
                raise Halt

            with self.assertRaises(Halt):
                IndexerRun(
                    store, IndexerOptions(start_url=START_URL, request_delay_seconds=0.0), FakeHttpClient(pages)
                ).run(progress=halt)
            self.assertEqual(store.load_meta(), meta)

    def test_merge_verdicts_folds_days_oldest_first_up_to_a_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalLibraryStore(temp_dir)
            store.append_verdicts("2026-10-01", [{"ss_id": "a", "score": 10}, {"ss_id": "gone", "score": 1}])
            store.append_verdicts("2026-10-05", [{"ss_id": "a", "score": 20}])
            records = {"a": {"ss_id": "a"}}

            counts = merge_verdicts(store, records, "2026-10-04")
            self.assertEqual((counts, records["a"]["judgment"]["score"]), ({"days": 1, "merged": 1, "unknown": 1}, 10))
            merge_verdicts(store, records)
            self.assertEqual(records["a"]["judgment"]["score"], 20)


def _judge(store: LocalLibraryStore, ss_id: str, score: int = 50) -> None:
    day = sorted(store.root.glob("queue/*.jsonl"))[-1].stem
    verdicts = store.root / "verdicts"
    verdicts.mkdir(exist_ok=True)
    (verdicts / f"{day}.jsonl").write_text(
        json.dumps({"ss_id": ss_id, "judged_at": f"{day}T12:00:00Z", "score": score}) + "\n",
        encoding="utf-8",
    )
