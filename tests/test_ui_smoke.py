import importlib.util
from pathlib import Path
from unittest import TestCase

from flat_searcher.filtering import ListingFilters
from flat_searcher.ui import DesktopUIConfig, UIDependencyError
from flat_searcher.ui.app import _filters_from_dict, _filters_to_js
from flat_searcher.ui.translations import translate, ui_strings

_HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


class UIFilterTests(TestCase):
    def test_room_slider_range_payload_builds_multi_room_filters(self) -> None:
        filters = _filters_from_dict(
            {
                "declared_rooms_min": 1,
                "declared_rooms_max": 3,
                "effective_private_rooms_min": 2,
                "effective_private_rooms_max": 4,
            }
        )

        self.assertEqual(filters.declared_rooms, frozenset({1, 2, 3}))
        self.assertEqual(filters.effective_private_rooms, frozenset({2, 3, 4}))

    def test_filters_to_js_exports_room_ranges_for_slider_controls(self) -> None:
        payload = _filters_to_js(
            ListingFilters(
                declared_rooms=frozenset({1, 2}),
                effective_private_rooms=frozenset({2, 3}),
            )
        )

        self.assertEqual(payload["declared_rooms_min"], 1)
        self.assertEqual(payload["declared_rooms_max"], 2)
        self.assertEqual(payload["effective_private_rooms_min"], 2)
        self.assertEqual(payload["effective_private_rooms_max"], 3)


class UITextResourceTests(TestCase):
    def test_ui_strings_are_available_by_stable_resource_id(self) -> None:
        strings = ui_strings("ru")

        self.assertEqual(strings["filter.price_range"], "Диапазон цены")
        self.assertEqual(strings["view.ranking"], "Рейтинг квартир")
        self.assertEqual(strings["summary.top_score"], "Лучший рейтинг")
        self.assertEqual(strings["ai_queue.title"], "Очередь ИИ")
        self.assertEqual(translate("en", "filter.price_range"), "Price Range")


class UIDependencyTests(TestCase):
    def test_run_desktop_app_requires_pyside6_when_missing(self) -> None:
        if _HAS_PYSIDE6:
            self.skipTest("PySide6 is installed; dependency guard cannot be exercised.")
        from flat_searcher.ui import run_desktop_app

        with self.assertRaises(UIDependencyError):
            run_desktop_app(DesktopUIConfig(database_path="ignored.sqlite3"))

class UITopbarLayoutTests(TestCase):
    def test_sync_progress_is_rendered_outside_the_topbar(self) -> None:
        html = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "web" / "index.html").read_text(encoding="utf-8")
        topbar = html.split('<header class="topbar">', 1)[1].split('</header>', 1)[0]

        self.assertNotIn("progressChip", topbar)
        self.assertNotIn("countChip", topbar)
        self.assertNotIn('id="syncStrip"', topbar)

class UISyncSignalRoutingTests(TestCase):
    def test_worker_progress_is_routed_through_bridge_slot_before_web_channel_signal(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("worker.progress.connect(self._on_pipeline_progress)", source)
        self.assertIn("def _on_pipeline_progress(self, payload_json: str)", source)
        self.assertNotIn("worker.progress.connect(self.pipelineProgress.emit)", source)

class UISyncProgressFallbackTests(TestCase):
    def test_sync_progress_has_dom_fallback_and_throttled_publish(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("_force_sync_strip_update", source)
        self.assertIn("runJavaScript(script)", source)
        self.assertIn("_pipeline_flush_timer", source)
        self.assertIn("clearHttpCache", source)

    def test_web_app_exposes_pipeline_status_hook_for_python_fallback(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("window.__flatSearcherApplyPipelineStatusRaw = applyPipelineStatusFromRaw", source)

class UIAIQueueControlTests(TestCase):
    def test_ai_queue_panel_exposes_start_and_stop_controls(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "web" / "app.js").read_text(encoding="utf-8")
        html = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("queueStartAnalysis", source)
        self.assertIn("queueStopAnalysis", source)
        self.assertIn('callJson("startAIQueue")', source)
        self.assertIn('callJson("stopAIQueue")', source)
        self.assertIn("app.js?v=", html)

    def test_backend_exposes_manual_ai_start_stop_slots(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("def startAIQueue(self)", source)
        self.assertIn("def stopAIQueue(self)", source)
        self.assertIn("request_stop", source)
        self.assertIn("ai_stopping", source)
        self.assertIn("ai_stopped", source)


class UIBackgroundScoringTests(TestCase):
    def test_backend_recalculates_scores_off_the_main_thread(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("class RecalcWorker", source)
        self.assertIn("def prepareProfile(self, profile_key: str)", source)
        self.assertIn("scoresReady = Signal(str)", source)
        self.assertNotIn("def _ensure_scores", source)

    def test_web_app_gates_reload_on_prepared_scores(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "flat_searcher" / "ui" / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('callJson("prepareProfile"', source)
        self.assertIn("bridge.scoresReady.connect(onScoresReady)", source)
