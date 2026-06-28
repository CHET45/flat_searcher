"""PySide6 desktop shell rendering the analytical web UI in a QWebEngineView."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from flat_searcher.ai import AIAnalysisPipeline, GeminiModelClient
from flat_searcher.config import DEFAULT_SS_START_URL, AppConfig
from flat_searcher.db.ai_repository import ListingForAnalysis
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.profile_repository import ProfileRepository
from flat_searcher.db.read_repository import ListingReadRepository
from flat_searcher.db.repository import open_database
from flat_searcher.db.session_repository import SearchSessionRepository
from flat_searcher.db.user_state_repository import UserStateRepository
from flat_searcher.filtering import ListingFilters
from flat_searcher.mapping import MapReferencePoint, build_map_markers
from flat_searcher.presentation import (
    DetailViewModel,
    WorkflowTab,
    filters_for_tab,
)
from flat_searcher.ranking import rank_candidates
from flat_searcher.geo.geocoder import NominatimGeocoder
from flat_searcher.geo.overpass import OverpassPOIProvider
from flat_searcher.images import ImageDownloader
from flat_searcher.scoring import (
    ImportanceLevel,
    ScoreBlockKey,
    custom_profile,
    slugify_profile_name,
)
from flat_searcher.scraper.http_client import HttpTextClient
from flat_searcher.services.ai_analysis import AIAnalysisProvider, AIAnalysisService, MockAIAnalysisProvider
from flat_searcher.services.geocoding import GeocodingRunResult, GeocodingService
from flat_searcher.services.gemini_analysis import GeminiAnalysisProvider
from flat_searcher.services.infrastructure import (
    InfrastructureRefreshResult,
    InfrastructureRefreshService,
)
from flat_searcher.services.location_scoring import LocationScoreService
from flat_searcher.services.processing import ListingProcessingResult
from flat_searcher.services.scoring import ScoreRecalculationService
from flat_searcher.services.sync import ListingSyncService, SyncResult
from flat_searcher.ui import payloads
from flat_searcher.ui.translations import LANGUAGES, translate, ui_strings

WEB_DIR = Path(__file__).resolve().parent / "web"
logger = logging.getLogger(__name__)


class UIDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesktopUIConfig:
    database_path: Path
    profile_key: str = "for_living_mortgage"
    start_url: str = DEFAULT_SS_START_URL
    language: str = "en"


def run_desktop_app(config: DesktopUIConfig) -> int:
    logger.info("UI app starting: database=%s start_url=%s profile=%s language=%s", config.database_path, config.start_url, config.profile_key, config.language)
    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as error:
        raise UIDependencyError(
            "PySide6 is not installed. Install the optional UI dependencies to run the desktop app."
        ) from error

    init_database(Path(config.database_path))

    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))
    window = _create_main_window(config)
    window.show()
    return app.exec()


def _create_main_window(config: DesktopUIConfig):
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QLabel, QMainWindow, QStatusBar

    class SyncWorker(QObject):
        progress = Signal(str)
        finished = Signal(object)
        failed = Signal(str)

        def __init__(
            self,
            database_path: Path,
            start_url: str,
            limit: int,
            profile_key: str,
        ) -> None:
            super().__init__()
            self._database_path = database_path
            self._start_url = start_url
            self._limit = limit
            self._profile_key = profile_key

        @Slot()
        def run(self) -> None:
            logger.info("UI sync worker entered: database=%s start_url=%s limit=%s profile=%s", self._database_path, self._start_url, self._limit, self._profile_key)
            try:
                limit = None if self._limit <= 0 else self._limit
                runtime_config = AppConfig.from_env(database_override=self._database_path)
                logger.info("UI sync worker runtime config resolved: database=%s log_file=%s", runtime_config.database_path, runtime_config.log_file)
                runtime_config.ensure_runtime_directories()

                def on_sync_progress(payload: dict[str, object]) -> None:
                    logger.debug("UI sync progress payload: %s", payload)
                    self._emit_progress(payload)

                logger.info("UI sync worker starting ListingSyncService.sync with limit=%s", limit)
                self._emit_progress({"stage": "sync_prepare", "message": "initializing_database"})
                sync_result = ListingSyncService(
                    database_path=self._database_path,
                    start_url=self._start_url,
                    http_client=HttpTextClient(timeout_seconds=20.0, request_delay_seconds=0.05),
                    list_fetch_workers=8,
                    detail_fetch_workers=6,
                ).sync(
                    limit=limit,
                    mark_missing_inactive=limit is None,
                    progress_callback=on_sync_progress,
                )
                logger.info("UI sync worker finished ListingSyncService.sync: %s", sync_result)
                self._emit_progress({"stage": "scoring"})
                logger.info("UI sync worker recalculating scores for profile=%s", self._profile_key)
                ScoreRecalculationService(self._database_path).recalculate(self._profile_key)
            except Exception as error:  # surfaced to the user as a toast
                logger.exception("UI sync worker failed")
                self.failed.emit(str(error))
                return
            logger.info("UI sync worker finished successfully")
            self.finished.emit(sync_result)

        def _emit_progress(self, payload: dict[str, object]) -> None:
            self.progress.emit(_dumps(payload))

    class AIQueueWorker(QObject):
        progress = Signal(str)
        finished = Signal(object)
        failed = Signal(str)

        def __init__(
            self,
            database_path: Path,
            profile_key: str,
            analysis_order: tuple[int, ...],
            force_listing_ids: frozenset[int],
        ) -> None:
            super().__init__()
            self._database_path = database_path
            self._profile_key = profile_key
            self._analysis_order = analysis_order
            self._force_listing_ids = force_listing_ids
            self._stop_requested = Event()

        @Slot()
        def request_stop(self) -> None:
            logger.info("UI AI queue worker stop requested")
            self._stop_requested.set()

        @Slot()
        def run(self) -> None:
            logger.info(
                "UI AI queue worker entered: database=%s profile=%s queued=%s force=%s",
                self._database_path,
                self._profile_key,
                len(self._analysis_order),
                sorted(self._force_listing_ids),
            )
            try:
                runtime_config = AppConfig.from_env(database_override=self._database_path)
                runtime_config.ensure_runtime_directories()
                provider = _build_ui_analysis_provider(runtime_config)

                def on_ai_progress(listing: ListingForAnalysis, index: int, total: int) -> None:
                    self._emit_progress(
                        {
                            "stage": "ai",
                            "current": index,
                            "total": total,
                            "listing": _listing_progress_label(listing),
                            "listingId": listing.listing_id,
                            "ssId": listing.ss_id,
                        }
                    )

                self._emit_progress(
                    {
                        "stage": "ai_prepare",
                        "current": 0,
                        "total": len(self._analysis_order),
                    }
                )
                ai_result = AIAnalysisService(
                    database_path=self._database_path,
                    provider=provider,
                ).analyze_ordered(
                    analysis_version="ui-auto-v1",
                    listing_ids=self._analysis_order,
                    force_listing_ids=self._force_listing_ids,
                    progress_callback=on_ai_progress,
                    should_cancel=self._stop_requested.is_set,
                )
                if ai_result.cancelled:
                    self._emit_progress(
                        {
                            "stage": "ai_stopped",
                            "checked": ai_result.checked_count,
                            "analyzed": ai_result.analyzed_count,
                            "failed": ai_result.failed_count,
                        }
                    )
                self._emit_progress({"stage": "location"})
                location_result = LocationScoreService(self._database_path).recalculate()
                self._emit_progress({"stage": "scoring"})
                scoring_result = ScoreRecalculationService(self._database_path).recalculate(
                    self._profile_key
                )
                result = ListingProcessingResult(
                    ai=ai_result,
                    location=location_result,
                    scoring=scoring_result,
                )
            except Exception as error:
                logger.exception("UI AI queue worker failed")
                self.failed.emit(str(error))
                return
            logger.info("UI AI queue worker finished successfully: %s", result)
            self.finished.emit(result)

        def _emit_progress(self, payload: dict[str, object]) -> None:
            self.progress.emit(_dumps(payload))

    class UiBridge(QObject):
        syncFinished = Signal(str)
        syncFailed = Signal(str)
        aiFinished = Signal(str)
        aiFailed = Signal(str)
        pipelineProgress = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self._language = config.language
            self._profile_key = config.profile_key
            self._scored: set[str] = set()
            self._sync_thread: QThread | None = None
            self._sync_worker: SyncWorker | None = None
            self._ai_thread: QThread | None = None
            self._ai_worker: AIQueueWorker | None = None
            self._ai_attempted_ids: set[int] = set()
            self._latest_pipeline_status: dict[str, object] | None = None
            self._last_pipeline_emit_json: str | None = None
            self._pipeline_flush_timer = QTimer(self)
            self._pipeline_flush_timer.setSingleShot(True)
            self._pipeline_flush_timer.setInterval(120)
            self._pipeline_flush_timer.timeout.connect(self._flush_pipeline_status)
            self._ai_queue_order: list[int] = []
            self._ai_reanalysis_ids: set[int] = set()
            self._ai_paused = False
            self._ai_auto_timer = QTimer(self)
            self._ai_auto_timer.setInterval(1500)
            self._ai_auto_timer.timeout.connect(self._maybe_start_ai_queue)
            self._ai_auto_timer.start()

        @Slot(result=str)
        def bootstrap(self) -> str:
            _ensure_scores(config.database_path, self._profile_key, self._scored)
            return _dumps(
                {
                    "strings": _strings(self._language),
                    "language": self._language,
                    "languages": [{"code": code, "label": label} for code, label in LANGUAGES],
                    "activeProfile": self._profile_key,
                    "profiles": _profiles_payload(config, self._language),
                    "sessions": _sessions_payload(config),
                    "districts": _districts(config),
                    "filterBounds": _filter_bounds(config),
                }
            )

        @Slot(str, result=str)
        def loadView(self, state_json: str) -> str:
            state = json.loads(state_json)
            self._profile_key = state.get("profileKey") or self._profile_key
            return _dumps(
                _build_view(
                    config,
                    self._language,
                    self._profile_key,
                    state.get("filters") or {},
                    state.get("tab") or "all",
                    self._scored,
                )
            )

        @Slot(int, str, result=str)
        def loadDetail(self, listing_id: int, profile_key: str) -> str:
            key = profile_key or self._profile_key
            return _dumps(_build_detail(config, self._language, key, listing_id))

        @Slot(int, result=str)
        def toggleFavorite(self, listing_id: int) -> str:
            _toggle_favorite(config, self._profile_key, listing_id)
            return "ok"

        @Slot(int, result=str)
        def toggleRejected(self, listing_id: int) -> str:
            _toggle_rejected(config, self._profile_key, listing_id)
            return "ok"

        @Slot(int, str, result=str)
        def saveNotes(self, listing_id: int, notes: str) -> str:
            _save_notes(config, listing_id, notes)
            return "ok"

        @Slot(str, str, result=str)
        def loadComparison(self, ids_json: str, profile_key: str) -> str:
            ids = [int(value) for value in json.loads(ids_json)]
            key = profile_key or self._profile_key
            return _dumps(_build_comparison(config, self._language, key, ids))

        @Slot(str, result=str)
        def loadProfileEditor(self, profile_key: str) -> str:
            return _dumps(_profile_editor(config, self._language, profile_key or self._profile_key))

        @Slot(str, str, str, result=str)
        def saveProfileImportance(self, base_key: str, name: str, importance_json: str) -> str:
            new_key = _save_profile_importance(config, base_key, name, json.loads(importance_json))
            self._profile_key = new_key
            self._scored.discard(new_key)
            _ensure_scores(config.database_path, new_key, self._scored)
            return _dumps(
                {"profiles": _profiles_payload(config, self._language), "activeProfile": new_key}
            )

        @Slot(str, result=str)
        def deleteProfile(self, key: str) -> str:
            _delete_profile(config, key)
            if self._profile_key == key:
                self._profile_key = "for_living_mortgage"
                _ensure_scores(config.database_path, self._profile_key, self._scored)
            return _dumps(
                {
                    "profiles": _profiles_payload(config, self._language),
                    "activeProfile": self._profile_key,
                }
            )

        @Slot(str, str, result=str)
        def renameProfile(self, key: str, name: str) -> str:
            _rename_profile(config, key, name)
            return _dumps({"profiles": _profiles_payload(config, self._language)})

        @Slot(str, result=str)
        def setProfile(self, key: str) -> str:
            self._profile_key = key
            _ensure_scores(config.database_path, key, self._scored)
            return "ok"

        @Slot(str, result=str)
        def setLanguage(self, code: str) -> str:
            self._language = code
            return "ok"

        @Slot(str, result=str)
        def saveSession(self, payload_json: str) -> str:
            data = json.loads(payload_json)
            _save_session(
                config, data.get("name", ""), data.get("profileKey"), data.get("filters") or {}
            )
            return _dumps({"sessions": _sessions_payload(config)})

        @Slot(int, result=str)
        def loadSession(self, session_id: int) -> str:
            return _dumps(_load_session(config, session_id))

        @Slot(int, result=str)
        def deleteSession(self, session_id: int) -> str:
            _delete_session(config, session_id)
            return _dumps({"sessions": _sessions_payload(config)})

        @Slot(str, result=str)
        def openExternal(self, url: str) -> str:
            import webbrowser

            if url:
                webbrowser.open(url)
            return "ok"

        @Slot(result=str)
        def loadAIQueue(self) -> str:
            payload = _ai_queue_payload(
                config,
                self._ai_queue_order,
                self._ai_reanalysis_ids,
            )
            self._ai_queue_order = list(payload["order"])
            self._ai_reanalysis_ids = set(payload["reanalysisIds"])
            return _dumps(payload)

        @Slot(str, str, result=str)
        def saveAIQueue(self, order_json: str, reanalysis_json: str) -> str:
            self._ai_queue_order = _parse_int_list(order_json)
            self._ai_reanalysis_ids = set(_parse_int_list(reanalysis_json))
            self._ai_attempted_ids.difference_update(self._ai_reanalysis_ids)
            payload = _ai_queue_payload(
                config,
                self._ai_queue_order,
                self._ai_reanalysis_ids,
            )
            self._ai_queue_order = list(payload["order"])
            self._ai_reanalysis_ids = set(payload["reanalysisIds"])
            self._maybe_start_ai_queue()
            return _dumps(payload)

        @Slot(result=str)
        def ensureAIQueueRunning(self) -> str:
            started = self._maybe_start_ai_queue(manual=False)
            return _dumps({"started": started, "busy": self._ai_thread is not None, "paused": self._ai_paused})

        @Slot(result=str)
        def startAIQueue(self) -> str:
            self._ai_paused = False
            started = self._maybe_start_ai_queue(manual=True)
            return _dumps({"started": started, "busy": self._ai_thread is not None, "paused": self._ai_paused})

        @Slot(result=str)
        def stopAIQueue(self) -> str:
            self._ai_paused = True
            if self._ai_worker is not None:
                self._set_pipeline_status({"stage": "ai_stopping"})
                self._emit_pipeline_status_now()
                self._ai_worker.request_stop()
                return _dumps({"stopping": True, "busy": True, "paused": self._ai_paused})
            self._set_pipeline_status({"stage": "ai_stopped", "checked": 0, "analyzed": 0, "failed": 0})
            self._emit_pipeline_status_now()
            return _dumps({"stopping": False, "busy": False, "paused": self._ai_paused})

        @Slot(result=str)
        def syncStatus(self) -> str:
            return _dumps(
                {
                    "busy": self._sync_thread is not None or self._ai_thread is not None,
                    "syncBusy": self._sync_thread is not None,
                    "aiBusy": self._ai_thread is not None,
                    "aiPaused": self._ai_paused,
                    "status": self._latest_pipeline_status,
                }
            )

        @Slot(int, result=str)
        def startSync(self, limit: int) -> str:
            logger.info("UI bridge startSync called: limit=%s active_thread=%s", limit, self._sync_thread is not None)
            if self._sync_thread is not None:
                logger.info("UI bridge startSync rejected: worker already busy")
                return "busy"
            self._set_pipeline_status({"stage": "sync_discover", "pages": 0, "listings": 0})
            self._emit_pipeline_status_now()
            thread = QThread(self)
            worker = SyncWorker(
                config.database_path,
                config.start_url,
                max(0, int(limit)),
                self._profile_key,
            )
            worker.moveToThread(thread)
            thread.started.connect(lambda: logger.info("UI sync thread started"))
            thread.started.connect(worker.run)
            worker.progress.connect(self._on_pipeline_progress)
            worker.finished.connect(self._on_sync_finished)
            worker.failed.connect(self._on_sync_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            thread.finished.connect(lambda: logger.info("UI sync thread finished"))
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_sync_thread)
            self._sync_thread = thread
            self._sync_worker = worker
            logger.info("UI bridge starting sync thread")
            thread.start()
            return "ok"

        def _on_sync_finished(self, result: SyncResult) -> None:
            logger.info("UI bridge sync finished signal: %s", result)
            self._set_pipeline_status({"stage": "finished"})
            self._emit_pipeline_status_now()
            self._scored.clear()
            self._scored.add(self._profile_key)
            message = translate(self._language, "sync.finished_summary").format(
                seen=result.seen_count,
                new=result.new_count,
                changed=result.changed_count,
            )
            self.syncFinished.emit(message)
            QTimer.singleShot(250, lambda: self._maybe_start_ai_queue(manual=False))

        def _on_sync_failed(self, message: str) -> None:
            logger.error("UI bridge sync failed signal: %s", message)
            self._set_pipeline_status({"stage": "failed", "message": message})
            self._emit_pipeline_status_now()
            self.syncFailed.emit(message)

        def _maybe_start_ai_queue(self, manual: bool = False) -> bool:
            if manual:
                self._ai_paused = False
            elif self._ai_paused:
                return False
            if self._ai_thread is not None:
                return False
            if self._sync_thread is not None:
                return False
            order, force_listing_ids = _analysis_order_for_run(
                config.database_path,
                tuple(self._ai_queue_order),
                frozenset(self._ai_reanalysis_ids),
            )
            order = tuple(listing_id for listing_id in order if listing_id not in self._ai_attempted_ids)
            force_listing_ids = frozenset(
                listing_id for listing_id in force_listing_ids if listing_id in set(order)
            )
            if not order:
                return False
            self._ai_queue_order = list(order)
            self._ai_reanalysis_ids = set(force_listing_ids)
            self._set_pipeline_status({"stage": "ai_prepare", "current": 0, "total": len(order)})
            self._emit_pipeline_status_now()

            thread = QThread(self)
            worker = AIQueueWorker(
                config.database_path,
                self._profile_key,
                order,
                force_listing_ids,
            )
            worker.moveToThread(thread)
            thread.started.connect(lambda: logger.info("UI AI queue thread started"))
            thread.started.connect(worker.run)
            worker.progress.connect(self._on_pipeline_progress)
            worker.finished.connect(lambda result, attempted=order, forced=force_listing_ids: self._on_ai_finished(result, attempted, forced))
            worker.failed.connect(lambda message, attempted=order: self._on_ai_failed(message, attempted))
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            thread.finished.connect(lambda: logger.info("UI AI queue thread finished"))
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_ai_thread)
            self._ai_thread = thread
            self._ai_worker = worker
            logger.info("UI bridge starting AI queue thread: queued=%s", len(order))
            thread.start()
            return True

        def _on_ai_finished(
            self,
            result: ListingProcessingResult,
            attempted_ids: tuple[int, ...],
            force_listing_ids: frozenset[int],
        ) -> None:
            logger.info("UI bridge AI queue finished signal: %s", result)
            processed_ids = attempted_ids[: max(0, result.ai.checked_count)]
            self._ai_attempted_ids.update(processed_ids)
            self._ai_reanalysis_ids.difference_update(force_listing_ids.intersection(processed_ids))
            self._scored.clear()
            self._scored.add(self._profile_key)
            if result.ai.cancelled:
                self._ai_paused = True
                self._set_pipeline_status(
                    {
                        "stage": "ai_stopped",
                        "checked": result.ai.checked_count,
                        "analyzed": result.ai.analyzed_count,
                        "failed": result.ai.failed_count,
                    }
                )
                message = translate(self._language, "ai_queue.stopped_summary").format(
                    analyzed=result.ai.analyzed_count,
                    failed=result.ai.failed_count,
                )
            else:
                self._set_pipeline_status(
                    {
                        "stage": "ai_finished",
                        "checked": result.ai.checked_count,
                        "analyzed": result.ai.analyzed_count,
                        "failed": result.ai.failed_count,
                    }
                )
                message = translate(self._language, "ai_queue.finished_summary").format(
                    analyzed=result.ai.analyzed_count,
                    failed=result.ai.failed_count,
                )
            self._emit_pipeline_status_now()
            self.aiFinished.emit(message)
            if not result.ai.cancelled:
                QTimer.singleShot(1500, lambda: self._maybe_start_ai_queue(manual=False))

        def _on_ai_failed(self, message: str, attempted_ids: tuple[int, ...]) -> None:
            logger.error("UI bridge AI queue failed signal: %s", message)
            self._ai_attempted_ids.update(attempted_ids)
            self._ai_paused = True
            self._set_pipeline_status({"stage": "failed", "message": message})
            self._emit_pipeline_status_now()
            self.aiFailed.emit(message)


        @Slot(str)
        def _on_pipeline_progress(self, payload_json: str) -> None:
            # Keep the authoritative status in Python and coalesce UI pushes.
            # A full SS sync can produce thousands of events per second when detail
            # pages are fetched in parallel; flooding QWebChannel prevents the web
            # side from repainting on some QWebEngine builds.
            logger.debug("UI bridge received sync progress: %s", payload_json)
            try:
                parsed = json.loads(payload_json)
                if isinstance(parsed, dict):
                    self._set_pipeline_status(parsed)
            except json.JSONDecodeError:
                logger.warning("UI bridge received invalid progress JSON: %s", payload_json)
                return
            self._schedule_pipeline_status_emit()

        def _set_pipeline_status(self, status: dict[str, object] | None) -> None:
            self._latest_pipeline_status = dict(status) if status is not None else None

        def _schedule_pipeline_status_emit(self) -> None:
            if not self._pipeline_flush_timer.isActive():
                self._pipeline_flush_timer.start()

        def _flush_pipeline_status(self) -> None:
            self._emit_pipeline_status_now()

        def _emit_pipeline_status_now(self) -> None:
            if self._latest_pipeline_status is None:
                payload_json = "null"
            else:
                payload_json = _dumps(self._latest_pipeline_status)
            if payload_json == self._last_pipeline_emit_json:
                return
            self._last_pipeline_emit_json = payload_json
            logger.debug("UI bridge publishing sync progress: %s", payload_json)
            self.pipelineProgress.emit(payload_json)

        def _clear_sync_thread(self) -> None:
            logger.info("UI bridge clearing sync thread state")
            self._sync_thread = None
            self._sync_worker = None

        def _clear_ai_thread(self) -> None:
            logger.info("UI bridge clearing AI queue thread state")
            self._ai_thread = None
            self._ai_worker = None

    class FlatSearcherWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Riga Analysis")
            self.resize(1440, 900)
            self.setMinimumSize(1120, 700)

            self._native_sync_last_json: str | None = None
            self._native_sync_was_busy = False
            self._native_sync_reload_pending = False

            self.native_sync_strip = QLabel("")
            self.native_sync_strip.setObjectName("nativePipelineStatus")
            self.native_sync_strip.setTextFormat(Qt.TextFormat.PlainText)
            self.native_sync_strip.setStyleSheet(
                "QLabel#nativePipelineStatus {"
                "color: #3f4a5d;"
                "font-size: 12px;"
                "padding: 0 8px;"
                "}"
            )

            self.view = QWebEngineView()
            settings = self.view.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )

            self.bridge = UiBridge()
            self.bridge.pipelineProgress.connect(self._force_sync_strip_update)
            self.channel = QWebChannel(self.view.page())
            self.channel.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(self.channel)

            self.setCentralWidget(self.view)
            status_bar = QStatusBar(self)
            status_bar.setObjectName("nativePipelineStatusBar")
            status_bar.setSizeGripEnabled(False)
            status_bar.setStyleSheet(
                "QStatusBar#nativePipelineStatusBar {"
                "background: #f4f6fa;"
                "border-top: 1px solid #d6dae3;"
                "}"
            )
            status_bar.addPermanentWidget(self.native_sync_strip, 1)
            self.setStatusBar(status_bar)
            status_bar.hide()

            self.native_sync_timer = QTimer(self)
            self.native_sync_timer.setInterval(200)
            self.native_sync_timer.timeout.connect(self._refresh_native_sync_strip)
            self.native_sync_timer.start()

            try:
                self.view.page().profile().clearHttpCache()
            except Exception:
                logger.debug("QWebEngine cache clear failed", exc_info=True)
            self.view.load(QUrl.fromLocalFile(str(WEB_DIR / "index.html")))

        @Slot(str)
        def _force_sync_strip_update(self, payload_json: str) -> None:
            self._apply_native_sync_payload_json(payload_json)
            # The web app receives progress through the QWebChannel signal, but on
            # some QWebEngine builds the signal does not repaint reliably, so push the
            # status straight into the JS state hook as a fallback.
            try:
                payload = json.loads(payload_json) if payload_json else None
            except json.JSONDecodeError:
                return
            if not isinstance(payload, dict):
                return
            status_arg = json.dumps(json.dumps(payload, ensure_ascii=False))
            script = (
                "(() => { if (window.__flatSearcherApplyPipelineStatusRaw) {"
                " try { window.__flatSearcherApplyPipelineStatusRaw(" + status_arg + "); }"
                " catch (error) {} } })();"
            )
            self.view.page().runJavaScript(script)

        def _refresh_native_sync_strip(self) -> None:
            status = self.bridge._latest_pipeline_status
            busy = self.bridge._sync_thread is not None or self.bridge._ai_thread is not None
            payload_json = _dumps(status) if isinstance(status, dict) else "null"
            current_key = f"{int(busy)}:{payload_json}"
            if current_key != self._native_sync_last_json:
                self._native_sync_last_json = current_key
                self._apply_native_sync_status(status if isinstance(status, dict) else None, busy)

            if self._native_sync_was_busy and not busy:
                self._native_sync_was_busy = False
                if not self._native_sync_reload_pending:
                    self._native_sync_reload_pending = True
                    self._set_native_status_text("Pipeline finished. Refreshing listings...")
                    QTimer.singleShot(250, self._reload_after_native_sync)
            elif busy:
                self._native_sync_was_busy = True

        def _reload_after_native_sync(self) -> None:
            self._native_sync_reload_pending = False
            self.view.reload()
            QTimer.singleShot(2500, lambda: self._set_native_status_text(""))

        def _set_native_status_text(self, text: str) -> None:
            self.native_sync_strip.setText(text)
            if self.statusBar() is not None:
                self.statusBar().setVisible(bool(text))

        def _apply_native_sync_payload_json(self, payload_json: str) -> None:
            try:
                payload = json.loads(payload_json) if payload_json else None
            except json.JSONDecodeError:
                payload = None
            busy = self.bridge._sync_thread is not None or self.bridge._ai_thread is not None
            self._apply_native_sync_status(payload if isinstance(payload, dict) else None, busy)

        def _apply_native_sync_status(self, status: dict[str, object] | None, busy: bool) -> None:
            if not status:
                self._set_native_status_text("" if not busy else "SS sync is running...")
                return
            text, meta, idle = _sync_status_display(status, self.bridge._language)
            if idle and not busy:
                self._set_native_status_text("")
                return
            parts = [part for part in (text, meta) if part]
            self._set_native_status_text(" · ".join(parts) if parts else "SS sync is running...")

    return FlatSearcherWindow()


def _tr(language: str):
    return lambda text: translate(language, text)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _sync_status_display(status: dict[str, object], language: str) -> tuple[str, str, bool]:
    stage = str(status.get("stage") or "")
    if stage == "sync_prepare":
        text = translate(language, "progress.sync_prepare")
        return text, text, False
    if stage == "ai_prepare":
        total = int(status.get("total") or 0)
        return translate(language, "progress.ai_prepare").format(total=total), "", False
    if stage == "sync_discover":
        pages = int(status.get("pages") or 0)
        listings = int(status.get("listings") or 0)
        total_pages = status.get("totalPages")
        if total_pages:
            text = translate(language, "progress.sync_discover_known").format(
                pages=pages, totalPages=int(total_pages), listings=listings
            )
            pages_text = f"{pages}/{int(total_pages)}"
        else:
            text = translate(language, "progress.sync_discover").format(
                pages=pages, listings=listings
            )
            pages_text = str(pages)
        last_page_count = int(status.get("lastPageCount") or 0)
        added = f" · +{last_page_count}" if last_page_count else ""
        meta = translate(language, "progress.sync_discover_meta").format(
            pages=pages_text, listings=listings, added=added
        )
        return text, meta, False
    if stage == "sync_list_page":
        text = translate(language, "progress.sync_list_page").format(
            page=int(status.get("page") or 1)
        )
        meta = translate(language, "progress.sync_list_page_meta").format(
            listings=int(status.get("listings") or 0)
        )
        return text, meta, False
    if stage == "sync":
        total = int(status.get("total") or 0)
        current = int(status.get("current") or 0)
        if total:
            text = translate(language, "progress.sync_with_count").format(
                current=current, total=total
            )
        else:
            text = translate(language, "progress.sync")
        parts: list[str] = []
        if isinstance(status.get("new"), int):
            parts.append(translate(language, "progress.sync_new").format(count=status["new"]))
        if isinstance(status.get("changed"), int):
            parts.append(translate(language, "progress.sync_updated").format(count=status["changed"]))
        if isinstance(status.get("failed"), int) and status.get("failed", 0) > 0:
            parts.append(translate(language, "progress.sync_failed").format(count=status["failed"]))
        if isinstance(status.get("aiQueued"), int):
            parts.append(f"AI queue: {status['aiQueued']}")
        return text, " · ".join(parts), False
    if stage == "ai":
        return translate(language, "progress.ai").format(
            current=int(status.get("current") or 0),
            total=int(status.get("total") or 0),
            listing=str(status.get("listing") or ""),
        ), "", False
    if stage == "ai_stopping":
        return translate(language, "progress.ai_stopping"), "", False
    if stage == "ai_stopped":
        return translate(language, "ai_queue.stopped_summary").format(
            analyzed=int(status.get("analyzed") or 0),
            failed=int(status.get("failed") or 0),
        ), "", False
    if stage == "location":
        return translate(language, "progress.location"), "", False
    if stage == "scoring":
        return translate(language, "progress.scoring"), "", False
    if stage == "ai_finished":
        return translate(language, "ai_queue.finished_summary").format(
            analyzed=int(status.get("analyzed") or 0),
            failed=int(status.get("failed") or 0),
        ), "", False
    if stage == "finished":
        return "", "", True
    if stage == "failed":
        return str(status.get("message") or translate(language, "status.failed")), "", False
    return "", "", True


def _build_ui_analysis_provider(config: AppConfig) -> AIAnalysisProvider:
    if not config.gemini_api_key:
        return MockAIAnalysisProvider()
    model_client = GeminiModelClient(
        api_key=config.gemini_api_key,
        model=config.gemini_model,
    )
    return GeminiAnalysisProvider(
        pipeline=AIAnalysisPipeline(model_client),
        image_downloader=ImageDownloader(
            temporary_images_dir=config.temporary_images_dir,
            floor_plans_dir=config.floor_plans_dir,
            fetcher=HttpTextClient(request_delay_seconds=0.2),
        ),
    )


def _listing_progress_label(listing: ListingForAnalysis) -> str:
    address_parts = [
        part
        for part in (
            listing.district,
            _street_with_house(listing.street, listing.house_number),
        )
        if part
    ]
    if address_parts:
        return ", ".join(address_parts)
    if listing.listing_title:
        return listing.listing_title
    return f"SS {listing.ss_id}"


def _street_with_house(street: str | None, house_number: str | None) -> str | None:
    if street and house_number:
        return f"{street} {house_number}"
    return street or house_number


def _strings(language: str) -> dict[str, str]:
    return ui_strings(language)


def _ensure_scores(database_path: Path, profile_key: str, scored: set[str]) -> None:
    if profile_key in scored:
        return
    try:
        ScoreRecalculationService(database_path).recalculate(profile_key)
    except ValueError:
        pass
    scored.add(profile_key)


def _filters_from_dict(data: dict) -> ListingFilters:
    def _single(value) -> frozenset:
        return frozenset({value}) if value not in (None, "", 0) else frozenset()

    return ListingFilters(
        price_min=data.get("price_min") or None,
        price_max=data.get("price_max") or None,
        area_min=float(data["area_min"]) if data.get("area_min") else None,
        area_max=float(data["area_max"]) if data.get("area_max") else None,
        districts=_single(data.get("district")),
        declared_rooms=_room_range(data, "declared_rooms") or _single(data.get("declared_rooms")),
        effective_private_rooms=_room_range(data, "effective_private_rooms")
        or _single(data.get("effective_private_rooms")),
        only_confirmed_layout=bool(data.get("only_confirmed_layout")),
        only_without_room_conflict=bool(data.get("only_without_room_conflict")),
        only_with_floor_plan=bool(data.get("only_with_floor_plan")),
        only_good_transport=bool(data.get("only_good_transport")),
        only_near_rtu=bool(data.get("only_near_rtu")),
        only_near_central_station=bool(data.get("only_near_central_station")),
        hide_high_mortgage_risk=bool(data.get("hide_high_mortgage_risk")),
        hide_stove_heating=bool(data.get("hide_stove_heating")),
        hide_wooden_buildings=bool(data.get("hide_wooden_buildings")),
        hide_viewed=bool(data.get("hide_viewed")),
    )


def _room_range(data: dict, prefix: str) -> frozenset[int]:
    minimum = data.get(f"{prefix}_min")
    maximum = data.get(f"{prefix}_max")
    if minimum in (None, "", 0) and maximum in (None, "", 0):
        return frozenset()
    min_value = int(minimum or 1)
    max_value = int(maximum or min_value)
    if min_value > max_value:
        min_value, max_value = max_value, min_value
    return frozenset(range(min_value, max_value + 1))


def _filters_to_js(filters: ListingFilters) -> dict:
    def _first(values) -> object:
        ordered = sorted(values)
        return ordered[0] if ordered else None

    def _range(values) -> tuple[int | None, int | None]:
        ordered = sorted(values)
        if not ordered:
            return None, None
        return ordered[0], ordered[-1]

    declared_min, declared_max = _range(filters.declared_rooms)
    effective_min, effective_max = _range(filters.effective_private_rooms)

    return {
        "price_min": filters.price_min,
        "price_max": filters.price_max,
        "area_min": int(filters.area_min) if filters.area_min else None,
        "area_max": int(filters.area_max) if filters.area_max else None,
        "district": _first(filters.districts),
        "declared_rooms": _first(filters.declared_rooms),
        "effective_private_rooms": _first(filters.effective_private_rooms),
        "declared_rooms_min": declared_min,
        "declared_rooms_max": declared_max,
        "effective_private_rooms_min": effective_min,
        "effective_private_rooms_max": effective_max,
        "only_confirmed_layout": filters.only_confirmed_layout,
        "only_without_room_conflict": filters.only_without_room_conflict,
        "only_with_floor_plan": filters.only_with_floor_plan,
        "only_good_transport": filters.only_good_transport,
        "only_near_rtu": filters.only_near_rtu,
        "only_near_central_station": filters.only_near_central_station,
        "hide_high_mortgage_risk": filters.hide_high_mortgage_risk,
        "hide_stove_heating": filters.hide_stove_heating,
        "hide_wooden_buildings": filters.hide_wooden_buildings,
        "hide_viewed": filters.hide_viewed,
    }


def _default_reference_points() -> tuple[MapReferencePoint, ...]:
    return (
        MapReferencePoint("rtu-main", 56.9505, 24.0837, "rtu", "map.rtu_main"),
        MapReferencePoint(
            "central-station", 56.9463, 24.1209, "station", "map.central_station_origo"
        ),
    )


def _build_view(
    config: DesktopUIConfig,
    language: str,
    profile_key: str,
    filters_dict: dict,
    tab_value: str,
    scored: set[str],
) -> dict:
    tr = _tr(language)
    _ensure_scores(config.database_path, profile_key, scored)
    filters = filters_for_tab(WorkflowTab(tab_value), _filters_from_dict(filters_dict))
    with open_database(config.database_path) as connection:
        repository = ListingReadRepository(connection)
        candidates = repository.load_candidates(profile_key)
        ranked = rank_candidates(candidates, filters)
        rows = payloads.ranking_rows_payload(ranked, tr)
        rows_by_id = {row["listingId"]: row for row in rows}
        visible_ids = {item.candidate.listing_id for item in ranked}
        markers = tuple(
            marker
            for marker in build_map_markers(repository.load_map_points(profile_key))
            if marker.listing_id in visible_ids
        )
        grocery = repository.load_grocery_reference_points(frozenset(visible_ids))
    references = (*_default_reference_points(), *grocery)
    return {
        "summary": payloads.summary_payload(ranked, len(candidates), tr),
        "rows": rows,
        "markers": payloads.map_markers_payload(markers, rows_by_id),
        "referencePoints": payloads.reference_points_payload(references, tr),
        "mapCoverage": {"visible": len(rows), "geocoded": len(markers)},
    }


def _build_detail(config: DesktopUIConfig, language: str, profile_key: str, listing_id: int):
    with open_database(config.database_path) as connection:
        repository = ListingReadRepository(connection)
        detail = repository.load_detail(listing_id, profile_key)
        if detail is None:
            return None
        if not detail.is_favorite and not detail.is_rejected:
            UserStateRepository(connection).mark_viewed(
                listing_id, datetime.now(timezone.utc).isoformat()
            )
            connection.commit()
        profile = ProfileRepository(connection).load_profile(profile_key)
    profile_name = profile.name if profile else profile_key
    floor_plan = _floor_plan_data_uri(detail.floor_plan_path)
    return payloads.detail_payload(detail, profile_name, floor_plan, _tr(language))


def _toggle_favorite(config: DesktopUIConfig, profile_key: str, listing_id: int) -> None:
    with open_database(config.database_path) as connection:
        detail = ListingReadRepository(connection).load_detail(listing_id, profile_key)
        if detail is None:
            return
        UserStateRepository(connection).set_favorite(listing_id, not detail.is_favorite)
        connection.commit()


def _toggle_rejected(config: DesktopUIConfig, profile_key: str, listing_id: int) -> None:
    with open_database(config.database_path) as connection:
        detail = ListingReadRepository(connection).load_detail(listing_id, profile_key)
        if detail is None:
            return
        UserStateRepository(connection).set_rejected(listing_id, not detail.is_rejected)
        connection.commit()


def _save_notes(config: DesktopUIConfig, listing_id: int, notes: str) -> None:
    with open_database(config.database_path) as connection:
        UserStateRepository(connection).update_notes(listing_id, notes.strip() or None)
        connection.commit()


def _build_comparison(
    config: DesktopUIConfig, language: str, profile_key: str, ids: list[int]
) -> dict:
    details = []
    with open_database(config.database_path) as connection:
        repository = ListingReadRepository(connection)
        for listing_id in ids:
            detail = repository.load_detail(listing_id, profile_key)
            if detail is not None:
                details.append(detail)
    if len(details) < 2:
        return {"columns": [], "rows": []}
    return payloads.comparison_payload(tuple(details[:5]), _tr(language))


def _profile_editor(config: DesktopUIConfig, language: str, profile_key: str) -> dict:
    with open_database(config.database_path) as connection:
        repository = ProfileRepository(connection)
        repository.sync_builtin_profiles()
        profile = repository.load_profile(profile_key)
    if profile is None:
        from flat_searcher.scoring import default_living_mortgage_profile

        profile = default_living_mortgage_profile()
    return payloads.profile_editor_payload(profile, _tr(language))


def _save_profile_importance(
    config: DesktopUIConfig, base_key: str, name: str, importance_raw: dict
) -> str:
    importance: dict[ScoreBlockKey, ImportanceLevel] = {}
    for key, value in importance_raw.items():
        try:
            importance[ScoreBlockKey(key)] = ImportanceLevel(value)
        except ValueError:
            continue
    with open_database(config.database_path) as connection:
        repository = ProfileRepository(connection)
        base = repository.load_profile(base_key)
        new_profile = custom_profile(
            key=slugify_profile_name(name),
            name=name.strip(),
            importance=importance,
            base_profile_key=base.key if base else None,
        )
        repository.save_profile(new_profile)
        connection.commit()
    return new_profile.key


def _delete_profile(config: DesktopUIConfig, key: str) -> None:
    with open_database(config.database_path) as connection:
        ProfileRepository(connection).delete_profile(key)
        connection.commit()


def _rename_profile(config: DesktopUIConfig, key: str, name: str) -> None:
    with open_database(config.database_path) as connection:
        ProfileRepository(connection).rename_profile(key, name.strip())
        connection.commit()


def _profiles_payload(config: DesktopUIConfig, language: str = "en") -> list[dict]:
    with open_database(config.database_path) as connection:
        repository = ProfileRepository(connection)
        repository.sync_builtin_profiles()
        summaries = repository.list_profiles()
    return [
        {
            "key": summary.profile_key,
            "name": _profile_display_name(language, summary.profile_name, summary.is_builtin),
            "builtin": summary.is_builtin,
        }
        for summary in summaries
    ]


def _profile_display_name(language: str, name: str, is_builtin: bool) -> str:
    return translate(language, name) if is_builtin else name


def _parse_int_list(raw: str) -> list[int]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    values: list[int] = []
    for value in parsed if isinstance(parsed, list) else []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in values:
            values.append(item)
    return values


def _analysis_order_for_run(
    database_path: Path,
    preferred_order: tuple[int, ...],
    reanalysis_ids: frozenset[int],
) -> tuple[tuple[int, ...], frozenset[int]]:
    payload = _ai_queue_payload_from_database(database_path, preferred_order, reanalysis_ids)
    return tuple(payload["order"]), frozenset(payload["reanalysisIds"])


def _ai_queue_payload(
    config: DesktopUIConfig,
    preferred_order: list[int],
    reanalysis_ids: set[int],
) -> dict[str, object]:
    return _ai_queue_payload_from_database(
        config.database_path,
        tuple(preferred_order),
        frozenset(reanalysis_ids),
    )


def _ai_queue_payload_from_database(
    database_path: Path,
    preferred_order: tuple[int, ...],
    reanalysis_ids: frozenset[int],
) -> dict[str, object]:
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT
                l.id,
                l.ss_id,
                l.listing_title,
                l.district,
                l.street,
                l.house_number,
                l.price_eur,
                l.area_m2,
                l.declared_rooms_ss,
                l.needs_ai_analysis,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM ai_analyses a
                        WHERE a.listing_id = l.id AND a.status = 'finished'
                    ) THEN 1
                    ELSE 0
                END AS has_analysis
            FROM listings l
            WHERE l.listing_status = 'active'
              AND l.description_text IS NOT NULL
            ORDER BY COALESCE(l.last_seen_at, l.updated_at, l.first_seen_at) DESC, l.id DESC
            """
        ).fetchall()

    rows_by_id = {int(row["id"]): row for row in rows}
    pending_ids = [
        int(row["id"])
        for row in rows
        if bool(row["needs_ai_analysis"]) or not bool(row["has_analysis"])
    ]
    valid_reanalysis_ids = frozenset(
        listing_id
        for listing_id in reanalysis_ids
        if listing_id in rows_by_id and bool(rows_by_id[listing_id]["has_analysis"])
    )
    eligible_ids = set(pending_ids) | set(valid_reanalysis_ids)
    queue_ids: list[int] = []
    for listing_id in (*preferred_order, *pending_ids, *valid_reanalysis_ids):
        if listing_id in eligible_ids and listing_id not in queue_ids:
            queue_ids.append(listing_id)

    queue = [
        _ai_queue_item(
            rows_by_id[listing_id],
            "reanalyze" if listing_id in valid_reanalysis_ids else "pending",
        )
        for listing_id in queue_ids
        if listing_id in rows_by_id
    ]
    queued_ids = set(queue_ids)
    analyzed_options = [
        _ai_queue_item(row, "analyzed")
        for row in rows
        if bool(row["has_analysis"]) and int(row["id"]) not in queued_ids
    ][:200]
    return {
        "queue": queue,
        "analyzedOptions": analyzed_options,
        "order": queue_ids,
        "reanalysisIds": sorted(valid_reanalysis_ids),
    }


def _ai_queue_item(row, status: str) -> dict[str, object]:
    return {
        "listingId": int(row["id"]),
        "title": _listing_title_from_row(row),
        "meta": _listing_meta_from_row(row),
        "status": status,
    }


def _listing_title_from_row(row) -> str:
    address_parts = [
        part
        for part in (
            row["district"],
            _street_with_house(row["street"], row["house_number"]),
        )
        if part
    ]
    if address_parts:
        return ", ".join(address_parts)
    if row["listing_title"]:
        return row["listing_title"]
    return f"SS {row['ss_id']}"


def _listing_meta_from_row(row) -> str:
    parts = []
    if row["price_eur"] is not None:
        parts.append(f"{int(row['price_eur']):,}".replace(",", " ") + " €")
    if row["area_m2"] is not None:
        area = float(row["area_m2"])
        parts.append(f"{area:g} m²")
    if row["declared_rooms_ss"] is not None:
        parts.append(f"SS {int(row['declared_rooms_ss'])}")
    return " · ".join(parts)


def _sessions_payload(config: DesktopUIConfig) -> list[dict]:
    with open_database(config.database_path) as connection:
        summaries = SearchSessionRepository(connection).list_sessions()
    return [{"id": summary.session_id, "name": summary.session_name} for summary in summaries]


def _save_session(
    config: DesktopUIConfig, name: str, profile_key: str | None, filters_dict: dict
) -> None:
    if not name.strip():
        return
    with open_database(config.database_path) as connection:
        SearchSessionRepository(connection).save_session(
            session_name=name.strip(),
            selected_profile_key=profile_key,
            filters=_filters_from_dict(filters_dict),
            sort_mode="score_desc",
        )
        connection.commit()


def _load_session(config: DesktopUIConfig, session_id: int) -> dict | None:
    with open_database(config.database_path) as connection:
        session = SearchSessionRepository(connection).load_session(session_id)
    if session is None:
        return None
    return {"filters": _filters_to_js(session.filters), "profileKey": session.selected_profile_key}


def _delete_session(config: DesktopUIConfig, session_id: int) -> None:
    with open_database(config.database_path) as connection:
        SearchSessionRepository(connection).delete_session(session_id)
        connection.commit()


def _districts(config: DesktopUIConfig) -> list[str]:
    with open_database(config.database_path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT district FROM listings "
            "WHERE district IS NOT NULL AND district != '' ORDER BY district"
        ).fetchall()
    return [row["district"] for row in rows]


def _filter_bounds(config: DesktopUIConfig) -> dict[str, dict[str, int]]:
    with open_database(config.database_path) as connection:
        row = connection.execute(
            """
            SELECT
                MIN(price_eur) AS min_price,
                MAX(price_eur) AS max_price,
                MIN(area_m2) AS min_area,
                MAX(area_m2) AS max_area,
                MIN(declared_rooms_ss) AS min_ss_rooms,
                MAX(declared_rooms_ss) AS max_ss_rooms,
                MIN(a.effective_private_rooms) AS min_ai_rooms,
                MAX(a.effective_private_rooms) AS max_ai_rooms
            FROM listings l
            LEFT JOIN latest_ai_analyses a ON a.listing_id = l.id
            WHERE l.listing_status = 'active'
            """
        ).fetchone()

    price_min = _floor_to_step(row["min_price"], 5_000, 0)
    price_max = _ceil_to_step(row["max_price"], 5_000, 300_000)
    area_min = _floor_to_step(row["min_area"], 5, 0)
    area_max = _ceil_to_step(row["max_area"], 5, 160)
    ss_room_min = int(row["min_ss_rooms"] or 1)
    ss_room_max = int(row["max_ss_rooms"] or 6)
    ai_room_min = int(row["min_ai_rooms"] or 1)
    ai_room_max = int(row["max_ai_rooms"] or ss_room_max or 6)
    if ss_room_max <= ss_room_min:
        ss_room_max = ss_room_min + 1
    if ai_room_max <= ai_room_min:
        ai_room_max = ai_room_min + 1

    return {
        "price": {"min": price_min, "max": max(price_min + 5_000, price_max), "step": 5_000},
        "area": {"min": area_min, "max": max(area_min + 5, area_max), "step": 1},
        "ssRooms": {
            "min": max(1, min(ss_room_min, ss_room_max)),
            "max": max(ss_room_min, ss_room_max, 1),
            "step": 1,
        },
        "aiRooms": {
            "min": max(1, min(ai_room_min, ai_room_max)),
            "max": max(ai_room_min, ai_room_max, 1),
            "step": 1,
        },
    }


def _floor_to_step(value: float | int | None, step: int, fallback: int) -> int:
    if value is None:
        return fallback
    return int(value // step * step)


def _ceil_to_step(value: float | int | None, step: int, fallback: int) -> int:
    if value is None:
        return fallback
    return int(((value + step - 1) // step) * step)


def _floor_plan_data_uri(path: str | None) -> str | None:
    if not path:
        return None
    floor_plan = Path(path)
    if not floor_plan.is_file():
        return None
    mime, _ = mimetypes.guess_type(floor_plan.name)
    encoded = base64.b64encode(floor_plan.read_bytes()).decode("ascii")
    return f"data:{mime or 'image/jpeg'};base64,{encoded}"


def _format_detail_text(
    view_model: DetailViewModel,
    language: str = "en",
    position: int | None = None,
) -> str:
    """Plain-text rendering of a listing detail, retained for CLI and tests."""

    def _t_detail(text: str) -> str:
        return translate(language, text)

    rating_lines = view_model.rating_lines
    if position is not None:
        rating_lines = (f"Position: #{position}", *rating_lines)

    sections = [
        _translate_display_text(language, view_model.title),
        f"{_t_detail('Original listing')}: {view_model.ss_url}",
        "",
        _section(_t_detail("Top"), view_model.top_lines, language),
        _section(_t_detail("Flags"), view_model.flags_lines, language),
        _section(_t_detail("Rating"), rating_lines, language),
        _section(_t_detail("Price value"), view_model.price_value_lines, language),
        _section(_t_detail("Layout"), view_model.layout_lines, language),
        _section(_t_detail("Mortgage"), view_model.mortgage_lines, language),
        _section(_t_detail("Location"), view_model.location_lines, language),
        _section(_t_detail("History"), view_model.history_lines, language),
        "",
        _t_detail("Original Listing Text"),
        view_model.original_listing_text,
    ]
    return "\n".join(section for section in sections if section is not None)


def _section(title: str, lines: tuple[str, ...], language: str = "en") -> str:
    translated_lines = (_translate_detail_line(language, line) for line in lines)
    return "\n".join((f"{title}:", *(f"  {line}" for line in translated_lines), ""))


def _translate_detail_line(language: str, line: str) -> str:
    if language == "en":
        return line
    label, separator, value = line.partition(":")
    if not separator:
        return _translate_display_text(language, line)
    return f"{translate(language, label)}:{_translate_display_text(language, value)}"


def _translate_display_text(language: str, text: str) -> str:
    if not text or language == "en":
        return text

    exact = translate(language, text)
    if exact != text:
        return exact

    if ", " in text:
        parts = [_translate_display_text(language, part) for part in text.split(", ")]
        return ", ".join(parts)

    if text.endswith(" private"):
        return text.removesuffix(" private") + " " + translate(language, "private")

    replacements = (
        ("Unknown district", translate(language, "Unknown district")),
        ("Unknown street", translate(language, "Unknown street")),
        ("EUR/m2", translate(language, "EUR/m2")),
        (" m2", " " + translate(language, "m2")),
        ("kitchen-living", translate(language, "kitchen-living")),
        ("area unknown", translate(language, "area unknown")),
        ("price unknown", translate(language, "price unknown")),
        ("not analyzed yet", translate(language, "not analyzed yet")),
        ("not calculated yet", translate(language, "not calculated yet")),
        ("unknown", translate(language, "unknown")),
        ("Unknown", translate(language, "Unknown")),
        ("Yes", translate(language, "Yes")),
        ("No", translate(language, "No")),
        ("none", translate(language, "none")),
        ("Confirmed", translate(language, "Confirmed")),
        ("Likely", translate(language, "Likely")),
        ("Unclear", translate(language, "Unclear")),
        ("Conflict", translate(language, "Conflict")),
        ("Critical", translate(language, "Critical")),
        ("Medium", translate(language, "Medium")),
        ("High", translate(language, "High")),
        ("Low", translate(language, "Low")),
    )
    translated = text
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated
