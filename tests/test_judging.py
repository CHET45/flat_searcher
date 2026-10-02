import json
import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.judging import (
    VERDICT_SCHEMA,
    JudgeFilters,
    JudgeOptions,
    JudgeRun,
    VerdictError,
    finalize_verdict,
    price_assessment,
)
from flat_searcher.library import LocalLibraryStore
from flat_searcher.scraper.http_client import BinaryFetchResult, FetchError

DAY = "2026-09-14"


def _record(ss_id: str, price: int = 100_000, rooms: int = 2, district: str = "Centrs",
            median: float | str = 2400.0, sample: int = 9, images: int = 2) -> dict:
    return {
        "ss_id": ss_id,
        "url": f"https://www.ss.com/msg/lv/{ss_id}.html",
        "status": "active",
        "core": {"price_eur": price, "area_m2": 50, "declared_rooms": rooms, "district": district},
        "fields": {"Cena": f"{price} €"},
        "text": {"title": "Flat", "description": "Nice."},
        "images": [{"url": f"https://i.ss.com/gallery/{ss_id}-{i}.jpg", "is_floor_plan": False} for i in range(images)],
        "stats": {"unique_visits": 3},
        "market": {"price_per_m2": price / 50, "district_median_price_per_m2": median, "district_sample_size": sample},
    }


def _entry(ss_id: str, **kwargs) -> dict:
    return {"ss_id": ss_id, "reason": "new", "changed": {}, "record": _record(ss_id, **kwargs)}


def _model_output(**overrides) -> dict:
    base = {
        "effective_private_rooms": 2, "walkthrough_rooms": 0, "kitchen_living": "no",
        "separate_kitchen": "yes", "layout_confidence": "likely", "layout_source": "photos",
        "rooms_conflict": "no", "layout_notes": "Two rooms off a hall.",
        "building_condition": "liveable", "wooden_building": "unknown", "stove_heating": "no",
        "mortgage_risk": "unknown", "mortgage_reasons": [], "price_ratio": 0.99,
        "price_verdict": "at", "price_basis": "district_median", "suspicious_price": "no",
        "score": 61, "verdict": "A plain two-room flat.",
    }
    return {**base, **overrides}


class FakeFetcher:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.urls: list[str] = []

    def fetch_bytes(self, url: str) -> BinaryFetchResult:
        self.urls.append(url)
        if url in self.failing:
            raise FetchError("gone")
        return BinaryFetchResult(url=url, content=b"\xff\xd8jpeg")


class FakeAsk:
    def __init__(self, outputs: dict[str, str] | None = None, default: str | None = None) -> None:
        self.outputs = outputs or {}
        self.default = default if default is not None else json.dumps(_model_output())
        self.calls: list[dict] = []

    def __call__(self, system: str, text: str, images, schema) -> str:
        entry = json.loads(text.split("Queue entry (one JSON object):\n", 1)[1].split("\n\nComputed", 1)[0])
        self.calls.append({"ss_id": entry["ss_id"], "images": len(images), "schema": schema, "text": text, "system": system})
        return self.outputs.get(entry["ss_id"], self.default)


def _setup(temp_dir: str, entries: list[dict]) -> tuple[LocalLibraryStore, Path]:
    store = LocalLibraryStore(temp_dir)
    store.write_queue(DAY, entries)
    instructions = Path(temp_dir) / "instructions.md"
    instructions.write_text("Judge honestly.", encoding="utf-8")
    return store, instructions


def _run(store, instructions, ask, fetcher=None, **options) -> object:
    opts = JudgeOptions(day=DAY, instructions_path=instructions, model_name="ollama/test", **options)
    return JudgeRun(store, opts, ask, fetcher or FakeFetcher()).run()


class PriceAssessmentTests(TestCase):
    def test_ratio_bands_and_suspicious_threshold(self) -> None:
        cases = [(0.60, "well_below", "yes"), (0.74, "well_below", "no"), (0.90, "below", "no"),
                 (1.00, "at", "no"), (1.30, "above", "no"), (1.50, "well_above", "no")]
        for ratio, verdict, suspicious in cases:
            record = _record("x", price=int(ratio * 2400 * 50), median=2400.0)
            assessment = price_assessment(record)
            self.assertEqual(assessment["price_verdict"], verdict, ratio)
            self.assertEqual(assessment["suspicious_price"], suspicious, ratio)
            self.assertEqual(assessment["price_basis"], "district_median")
            self.assertAlmostEqual(assessment["price_ratio"], ratio, places=2)

    def test_thin_sample_and_no_baseline(self) -> None:
        thin = price_assessment(_record("x", median="unknown", sample=3))
        self.assertEqual(thin, {"price_ratio": None, "price_verdict": "unknown",
                                "price_basis": "thin_sample", "suspicious_price": "unknown"})
        none = price_assessment(_record("x", median="unknown", sample=0))
        self.assertEqual(none["price_basis"], "no_baseline")


class FinalizeVerdictTests(TestCase):
    def test_envelope_price_override_and_unknowns(self) -> None:
        entry = _entry("a1", price=int(0.74 * 2400 * 50))
        raw = _model_output(price_ratio=0.5, price_verdict="at", wooden_building="unknown", walkthrough_rooms=None)

        verdict = finalize_verdict(raw, entry, model="ollama/test", judged_at="2026-09-14T10:00:00Z")

        self.assertEqual(verdict["ss_id"], "a1")
        self.assertEqual(verdict["model"], "ollama/test")
        self.assertEqual(verdict["version"], "v1")
        self.assertEqual(verdict["price_ratio"], 0.74)
        self.assertEqual(verdict["price_verdict"], "well_below")
        self.assertEqual(verdict["unknowns"], ["walkthrough_rooms", "wooden_building", "mortgage_risk"])

    def test_missing_key_and_bad_score_are_rejected(self) -> None:
        entry = _entry("a1")
        with self.assertRaises(VerdictError):
            finalize_verdict({k: v for k, v in _model_output().items() if k != "score"}, entry, model="m", judged_at="t")
        with self.assertRaises(VerdictError):
            finalize_verdict(_model_output(score=150), entry, model="m", judged_at="t")
        with self.assertRaises(VerdictError):
            finalize_verdict(_model_output(effective_private_rooms="two"), entry, model="m", judged_at="t")


class JudgeRunTests(TestCase):
    def test_judges_every_pending_entry_and_appends_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1"), _entry("b2", images=3)])
            ask = FakeAsk()
            fetcher = FakeFetcher()

            result = _run(store, instructions, ask, fetcher)

            self.assertEqual((result.judged, result.failed, result.aborted), (2, 0, False))
            verdicts = store.read_verdicts(DAY)
            self.assertEqual([v["ss_id"] for v in verdicts], ["a1", "b2"])
            self.assertEqual(verdicts[0]["score"], 61)
            self.assertEqual([c["images"] for c in ask.calls], [2, 3])
            self.assertIs(ask.calls[0]["schema"], VERDICT_SCHEMA)
            self.assertEqual(ask.calls[0]["system"], "Judge honestly.")
            self.assertIn('"price_ratio": 0.83, "price_verdict": "below"', ask.calls[0]["text"])
            self.assertEqual(verdicts[0]["price_verdict"], "below")

    def test_already_judged_entries_are_skipped_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1"), _entry("b2")])
            _run(store, instructions, FakeAsk(), limit=1)
            ask = FakeAsk()

            result = _run(store, instructions, ask)

            self.assertEqual(result.already_judged, 1)
            self.assertEqual([c["ss_id"] for c in ask.calls], ["b2"])
            self.assertEqual([v["ss_id"] for v in store.read_verdicts(DAY)], ["a1", "b2"])

    def test_filters_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            entries = [_entry("a1", price=90_000, rooms=1), _entry("b2", price=90_000, rooms=2, district="Teika"),
                       _entry("c3", price=200_000, rooms=3), _entry("d4", price=95_000, rooms=2)]
            store, instructions = _setup(temp_dir, entries)
            ask = FakeAsk()

            result = _run(store, instructions, ask,
                          filters=JudgeFilters(max_price=100_000, min_rooms=2, districts=frozenset({"centrs"})),
                          limit=5)

            self.assertEqual(result.eligible, 1)
            self.assertEqual([c["ss_id"] for c in ask.calls], ["d4"])

    def test_invalid_model_output_is_retried_once_then_counted_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1"), _entry("b2")])
            ask = FakeAsk(outputs={"a1": "not json"})

            result = _run(store, instructions, ask)

            self.assertEqual((result.judged, result.failed), (1, 1))
            self.assertEqual([v["ss_id"] for v in store.read_verdicts(DAY)], ["b2"])
            a1_calls = [c for c in ask.calls if c["ss_id"] == "a1"]
            self.assertEqual(len(a1_calls), 2)
            self.assertIn("Keep layout_notes under 60 words", a1_calls[1]["text"])
            self.assertNotIn("Keep layout_notes", a1_calls[0]["text"])

    def test_truncated_output_recovers_on_the_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1")])
            good = json.dumps(_model_output())

            class FlakyAsk(FakeAsk):
                def __call__(self, system, text, images, schema):
                    super().__call__(system, text, images, schema)
                    return good if "Keep layout_notes" in text else good[:-40]

            ask = FlakyAsk()
            result = _run(store, instructions, ask)

            self.assertEqual((result.judged, result.failed), (1, 0))
            self.assertEqual(len(ask.calls), 2)

    def test_three_consecutive_failures_abort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry(f"x{i}") for i in range(6)])
            ask = FakeAsk(default="{}")

            result = _run(store, instructions, ask)

            self.assertTrue(result.aborted)
            self.assertEqual(result.failed, 3)
            self.assertEqual(len(ask.calls), 6)
            self.assertIn("3 consecutive failures", result.abort_reason)

    def test_dead_image_is_skipped_and_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1", images=3)])
            ask = FakeAsk()
            fetcher = FakeFetcher(failing={"https://i.ss.com/gallery/a1-1.jpg"})

            result = _run(store, instructions, ask, fetcher, dry_run=True)

            self.assertEqual(result.judged, 1)
            self.assertEqual(ask.calls[0]["images"], 2)
            self.assertEqual(store.read_verdicts(DAY), [])

    def test_max_images_caps_the_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1", images=12)])
            ask = FakeAsk()

            _run(store, instructions, ask, max_images=4)

            self.assertEqual(ask.calls[0]["images"], 4)


class ImageSelectionTests(TestCase):
    def test_small_gallery_is_sent_whole_and_large_gallery_keeps_head_and_tail(self) -> None:
        from flat_searcher.judging.runner import select_image_positions

        self.assertEqual(select_image_positions(5, 8), [0, 1, 2, 3, 4])
        self.assertEqual(select_image_positions(20, 8), [0, 1, 2, 15, 16, 17, 18, 19])
        self.assertEqual(select_image_positions(20, 2), [0, 1])
        self.assertEqual(select_image_positions(0, 8), [])
        self.assertEqual(select_image_positions(20, 0), [])

    def test_runner_tells_the_model_which_positions_it_sees(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1", images=20)])
            ask = FakeAsk()
            fetcher = FakeFetcher()

            _run(store, instructions, ask, fetcher)

            self.assertEqual(ask.calls[0]["images"], 8)
            self.assertIn("gallery positions 1, 2, 3, 16, 17, 18, 19, 20", ask.calls[0]["text"])
            self.assertTrue(fetcher.urls[-1].endswith("a1-19.jpg"))


class LandTenureHintTests(TestCase):
    def test_leased_land_sentences_are_extracted_and_owned_land_is_kept_apart(self) -> None:
        from flat_searcher.judging.hints import hints_for, land_tenure_sentences

        leased = _record("a1")
        leased["text"]["description"] = (
            "Renovēta kāpņu telpa. Zeme zem mājas ir nomā. Autostāvvieta pie mājas."
        )
        fee = _record("a2")
        fee["text"]["description"] = "Labs stāvoklis + zemes noma 45 EUR/gadā + pārvaldītājs kooperatīvs."
        owned = _record("a3")
        owned["text"]["description"] = "Zeme ir īpašumā - bez papildus nomas maksas par zemi. Silts."
        none = _record("a4")
        none["text"]["description"] = "Saulains dzīvoklis ar balkonu."

        self.assertEqual(land_tenure_sentences(leased), ["Zeme zem mājas ir nomā."])
        self.assertEqual(land_tenure_sentences(fee), ["Labs stāvoklis + zemes noma 45 EUR/gadā + pārvaldītājs kooperatīvs."])
        self.assertEqual(land_tenure_sentences(owned), ["Zeme ir īpašumā - bez papildus nomas maksas par zemi."])
        self.assertEqual(hints_for(none), {})

    def test_hints_reach_the_model_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            entry = _entry("a1")
            entry["record"]["text"]["description"] = "Zeme zem mājas ir nomā."
            store, instructions = _setup(temp_dir, [entry])
            ask = FakeAsk()

            _run(store, instructions, ask)

            self.assertIn('"hints": {"land_tenure": ["Zeme zem mājas ir nomā."]}', ask.calls[0]["text"])


class TargetedRejudgeTests(TestCase):
    def test_ids_and_force_rejudge_only_the_named_listings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1"), _entry("b2"), _entry("c3")])
            _run(store, instructions, FakeAsk())
            ask = FakeAsk(default=json.dumps(_model_output(score=11)))

            result = _run(store, instructions, ask, only_ids=frozenset({"b2"}), force=True)

            self.assertEqual((result.eligible, result.judged), (1, 1))
            self.assertEqual([c["ss_id"] for c in ask.calls], ["b2"])
            verdicts = store.read_verdicts(DAY)
            self.assertEqual([v["ss_id"] for v in verdicts], ["a1", "b2", "c3", "b2"])
            self.assertEqual(verdicts[-1]["score"], 11)


class ShardTests(TestCase):
    def test_shards_partition_the_pending_queue_without_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry(f"x{i}") for i in range(7)])
            first, second = FakeAsk(), FakeAsk()

            # Both judges start from the same queue state, as concurrent processes do.
            _run(store, instructions, first, shard=(0, 2), dry_run=True)
            _run(store, instructions, second, shard=(1, 2), dry_run=True)

            seen_first = [c["ss_id"] for c in first.calls]
            seen_second = [c["ss_id"] for c in second.calls]
            self.assertEqual(seen_first, ["x0", "x2", "x4", "x6"])
            self.assertEqual(seen_second, ["x1", "x3", "x5"])
            self.assertEqual(sorted(seen_first + seen_second), [f"x{i}" for i in range(7)])


class PromptBudgetTests(TestCase):
    def test_long_description_is_trimmed_in_the_prompt_but_hints_use_the_full_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            entry = _entry("a1")
            entry["record"]["text"]["description"] = "x" * 5000 + ". Zeme zem mājas ir nomā."
            entry["record"]["fields"]["Apraksts"] = "y" * 500
            store, instructions = _setup(temp_dir, [entry])
            ask = FakeAsk()

            _run(store, instructions, ask)

            text = ask.calls[0]["text"]
            self.assertIn("[…truncated]", text)
            self.assertNotIn("x" * 3100, text)
            self.assertNotIn("y" * 200, text)
            self.assertIn('"land_tenure": ["Zeme zem mājas ir nomā."]', text)

    def test_context_overflow_retries_with_fewer_images_then_gives_up(self) -> None:
        from flat_searcher.judging.errors import PromptTooLargeError

        with tempfile.TemporaryDirectory() as temp_dir:
            store, instructions = _setup(temp_dir, [_entry("a1", images=8), _entry("b2", images=8)])
            good = json.dumps(_model_output())

            class ShrinkingAsk(FakeAsk):
                def __call__(self, system, text, images, schema):
                    super().__call__(system, text, images, schema)
                    ss_id = self.calls[-1]["ss_id"]
                    if ss_id == "a1" and len(images) > 2:
                        raise PromptTooLargeError("context size")
                    if ss_id == "b2":
                        raise PromptTooLargeError("context size")
                    return good

            ask = ShrinkingAsk()
            result = _run(store, instructions, ask)

            a1 = [c["images"] for c in ask.calls if c["ss_id"] == "a1"]
            b2 = [c["images"] for c in ask.calls if c["ss_id"] == "b2"]
            self.assertEqual(a1, [8, 4, 2])
            self.assertEqual(b2, [8, 4, 2])
            self.assertEqual((result.judged, result.failed), (1, 1))

    def test_run_on_paragraph_hint_is_a_window_around_the_phrase(self) -> None:
        from flat_searcher.judging.hints import land_tenure_sentences

        record = _record("a1")
        record["text"]["description"] = "y" * 2000 + " zeme zem mājas ir nomā " + "z" * 2000

        hints = land_tenure_sentences(record)

        self.assertEqual(len(hints), 1)
        self.assertLess(len(hints[0]), 260)
        self.assertIn("zeme zem mājas ir nomā", hints[0])

    def test_changed_block_keeps_scalars_and_shrinks_long_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            entry = _entry("a1")
            entry["reason"] = "changed"
            old_images = [{"url": f"https://i.ss.com/gallery/old-{i}.jpg", "is_floor_plan": False} for i in range(20)]
            entry["changed"] = {
                "core.price_eur": [100000, 95000],
                "text.description": ["a" * 5000, "b" * 5000],
                "images": [old_images, old_images + [{"url": "https://i.ss.com/gallery/new.jpg", "is_floor_plan": True}]],
            }
            store, instructions = _setup(temp_dir, [entry])
            ask = FakeAsk()

            _run(store, instructions, ask)

            text = ask.calls[0]["text"]
            self.assertIn('"core.price_eur": [100000, 95000]', text)
            self.assertNotIn("a" * 300, text)
            self.assertIn("…", text)
            self.assertIn('"images": ["20 images", "21 images"]', text)
            self.assertNotIn("gallery/old-19", text)


class OllamaClientTests(TestCase):
    def _payload(self, done_reason: str, eval_count: int) -> bytes:
        return json.dumps({
            "message": {"role": "assistant", "content": '{"effective_private_rooms": '},
            "done": True, "done_reason": done_reason, "eval_count": eval_count, "prompt_eval_count": 12172,
        }).encode("utf-8")

    def _ask(self, body: bytes) -> str:
        from unittest.mock import patch

        from flat_searcher.judging.ollama import OllamaJudgeClient

        class Response:
            status = 200

            def read(self) -> bytes:
                return body

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                pass

        with patch("flat_searcher.judging.ollama.urlopen", return_value=Response()):
            return OllamaJudgeClient().ask("system", "text", [], VERDICT_SCHEMA)

    def test_output_cut_by_a_full_context_is_a_prompt_too_large_error(self) -> None:
        from flat_searcher.judging.errors import PromptTooLargeError

        with self.assertRaises(PromptTooLargeError):
            self._ask(self._payload("length", 116))

    def test_output_cut_by_the_generation_cap_is_returned_for_the_loop_retry(self) -> None:
        self.assertEqual(self._ask(self._payload("length", 1200)), '{"effective_private_rooms": ')

    def test_complete_output_is_returned(self) -> None:
        self.assertEqual(self._ask(self._payload("stop", 418)), '{"effective_private_rooms": ')
