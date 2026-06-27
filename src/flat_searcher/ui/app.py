"""Optional PySide6 desktop UI shell."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.read_repository import ListingReadRepository
from flat_searcher.db.repository import open_database
from flat_searcher.db.user_state_repository import UserStateRepository
from flat_searcher.filtering import ListingFilters
from flat_searcher.mapping import build_map_markers
from flat_searcher.presentation import (
    WORKFLOW_TAB_LABELS,
    DetailViewModel,
    WorkflowTab,
    detail_view_model,
    filters_for_tab,
    ranking_row_view_model,
)
from flat_searcher.ranking import rank_candidates
from flat_searcher.ui.map_html import build_leaflet_html


class UIDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesktopUIConfig:
    database_path: Path
    profile_key: str = "for_living_mortgage"


RANKING_COLUMNS = (
    "#",
    "Score",
    "Title",
    "Price",
    "EUR/m2",
    "Area",
    "Layout",
    "Mortgage",
    "Status",
    "Flags",
)


def run_desktop_app(config: DesktopUIConfig) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as error:
        raise UIDependencyError(
            "PySide6 is not installed. Install the optional UI dependencies to run the desktop app."
        ) from error

    app = QApplication([])
    window = _create_main_window(config)
    window.show()
    return app.exec()


def _create_main_window(config: DesktopUIConfig):
    from PySide6.QtCore import QObject, Qt, Slot
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabBar,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    class MapBridge(QObject):
        def __init__(self, window) -> None:
            super().__init__(window)
            self.window = window

        @Slot(int)
        def markerSelected(self, listing_id: int) -> None:
            self.window.select_listing(listing_id)

    class FlatSearcherWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Flat Searcher")
            self.resize(1360, 820)
            self._rows = []
            self._map_ready = False
            self._current_listing_id: int | None = None

            root = QWidget()
            root_layout = QVBoxLayout(root)

            self.tab_bar = QTabBar()
            self._workflow_tabs = list(WorkflowTab)
            for tab in self._workflow_tabs:
                self.tab_bar.addTab(WORKFLOW_TAB_LABELS[tab])
            self.tab_bar.currentChanged.connect(lambda _index: self.reload_data())

            toolbar = QHBoxLayout()
            self.summary_label = QLabel("")
            refresh_button = QPushButton("Refresh")
            refresh_button.clicked.connect(self.reload_data)
            toolbar.addWidget(self.summary_label)
            toolbar.addStretch()
            toolbar.addWidget(refresh_button)

            content_splitter = QSplitter(Qt.Orientation.Horizontal)
            content_splitter.addWidget(self._build_filter_panel())

            self.tabs = QTabWidget()
            self.tabs.addTab(self._build_ranking_tab(), "Ranking")
            self.tabs.addTab(self._build_map_tab(), "Map")
            content_splitter.addWidget(self.tabs)
            content_splitter.setStretchFactor(0, 0)
            content_splitter.setStretchFactor(1, 1)
            content_splitter.setSizes([260, 1100])

            root_layout.addWidget(self.tab_bar)
            root_layout.addLayout(toolbar)
            root_layout.addWidget(content_splitter)
            self.setCentralWidget(root)

            self._load_district_options()
            self.reload_data()

        # ---- Layout construction -------------------------------------------------

        def _build_filter_panel(self) -> QWidget:
            panel = QGroupBox("Filters")
            form = QFormLayout(panel)

            self.price_min = QSpinBox()
            self.price_max = QSpinBox()
            for box in (self.price_min, self.price_max):
                box.setRange(0, 10_000_000)
                box.setSingleStep(5_000)
                box.setGroupSeparatorShown(True)
            self.price_max.setValue(0)

            self.area_min = QSpinBox()
            self.area_max = QSpinBox()
            for box in (self.area_min, self.area_max):
                box.setRange(0, 1000)
                box.setSuffix(" m2")

            self.district_combo = QComboBox()
            self.district_combo.addItem("All districts", userData=None)

            self.only_confirmed_layout = QCheckBox("Only confirmed layout")
            self.only_without_conflict = QCheckBox("Only without room conflict")
            self.only_floor_plan = QCheckBox("Only with floor plan")
            self.hide_high_mortgage = QCheckBox("Hide high mortgage risk")
            self.hide_stove = QCheckBox("Hide stove heating")
            self.hide_wooden = QCheckBox("Hide wooden buildings")
            self.hide_viewed = QCheckBox("Hide viewed")

            form.addRow("Price from", self.price_min)
            form.addRow("Price to", self.price_max)
            form.addRow("Area from", self.area_min)
            form.addRow("Area to", self.area_max)
            form.addRow("District", self.district_combo)
            form.addRow(self.only_confirmed_layout)
            form.addRow(self.only_without_conflict)
            form.addRow(self.only_floor_plan)
            form.addRow(self.hide_high_mortgage)
            form.addRow(self.hide_stove)
            form.addRow(self.hide_wooden)
            form.addRow(self.hide_viewed)

            apply_button = QPushButton("Apply filters")
            apply_button.clicked.connect(self.reload_data)
            clear_button = QPushButton("Clear filters")
            clear_button.clicked.connect(self._clear_filters)
            form.addRow(apply_button)
            form.addRow(clear_button)
            return panel

        def _build_ranking_tab(self) -> QWidget:
            ranking_splitter = QSplitter(Qt.Orientation.Horizontal)

            self.table = QTableWidget(0, len(RANKING_COLUMNS))
            self.table.setHorizontalHeaderLabels(list(RANKING_COLUMNS))
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.itemSelectionChanged.connect(self.show_selected_detail)

            detail_widget = QWidget()
            detail_layout = QVBoxLayout(detail_widget)

            actions = QHBoxLayout()
            self.favorite_button = QPushButton("Favorite")
            self.favorite_button.clicked.connect(self._toggle_favorite)
            self.reject_button = QPushButton("Reject")
            self.reject_button.clicked.connect(self._toggle_rejected)
            self.open_button = QPushButton("Open on SS.com")
            self.open_button.clicked.connect(self._open_in_browser)
            actions.addWidget(self.favorite_button)
            actions.addWidget(self.reject_button)
            actions.addWidget(self.open_button)
            actions.addStretch()

            self.detail_text = QPlainTextEdit()
            self.detail_text.setReadOnly(True)

            notes_label = QLabel("Notes")
            self.notes_edit = QPlainTextEdit()
            self.notes_edit.setPlaceholderText("Call seller, check heating, ask about land...")
            self.notes_edit.setFixedHeight(90)
            save_notes_button = QPushButton("Save notes")
            save_notes_button.clicked.connect(self._save_notes)

            detail_layout.addLayout(actions)
            detail_layout.addWidget(self.detail_text, stretch=1)
            detail_layout.addWidget(notes_label)
            detail_layout.addWidget(self.notes_edit)
            detail_layout.addWidget(save_notes_button)

            ranking_splitter.addWidget(self.table)
            ranking_splitter.addWidget(detail_widget)
            ranking_splitter.setSizes([720, 460])
            return ranking_splitter

        def _build_map_tab(self) -> QWidget:
            map_splitter = QSplitter(Qt.Orientation.Horizontal)
            self.map_view = QWebEngineView()
            self.map_detail_text = QPlainTextEdit()
            self.map_detail_text.setReadOnly(True)
            map_splitter.addWidget(self.map_view)
            map_splitter.addWidget(self.map_detail_text)
            map_splitter.setSizes([900, 380])

            self.map_bridge = MapBridge(self)
            self.map_channel = QWebChannel(self.map_view.page())
            self.map_channel.registerObject("mapBridge", self.map_bridge)
            self.map_view.page().setWebChannel(self.map_channel)
            self.map_view.loadFinished.connect(self._map_loaded)
            return map_splitter

        # ---- Filters -------------------------------------------------------------

        def current_tab(self) -> WorkflowTab:
            return self._workflow_tabs[self.tab_bar.currentIndex()]

        def _base_filters(self) -> ListingFilters:
            district = self.district_combo.currentData()
            return ListingFilters(
                price_min=self.price_min.value() or None,
                price_max=self.price_max.value() or None,
                area_min=float(self.area_min.value()) or None,
                area_max=float(self.area_max.value()) or None,
                districts=frozenset({district}) if district else frozenset(),
                only_confirmed_layout=self.only_confirmed_layout.isChecked(),
                only_without_room_conflict=self.only_without_conflict.isChecked(),
                only_with_floor_plan=self.only_floor_plan.isChecked(),
                hide_high_mortgage_risk=self.hide_high_mortgage.isChecked(),
                hide_stove_heating=self.hide_stove.isChecked(),
                hide_wooden_buildings=self.hide_wooden.isChecked(),
                hide_viewed=self.hide_viewed.isChecked(),
            )

        def _active_filters(self) -> ListingFilters:
            return filters_for_tab(self.current_tab(), self._base_filters())

        def _clear_filters(self) -> None:
            for box in (self.price_min, self.price_max, self.area_min, self.area_max):
                box.setValue(0)
            self.district_combo.setCurrentIndex(0)
            for checkbox in (
                self.only_confirmed_layout,
                self.only_without_conflict,
                self.only_floor_plan,
                self.hide_high_mortgage,
                self.hide_stove,
                self.hide_wooden,
                self.hide_viewed,
            ):
                checkbox.setChecked(False)
            self.reload_data()

        def _load_district_options(self) -> None:
            init_database(config.database_path)
            with open_database(config.database_path) as connection:
                rows = connection.execute(
                    "SELECT DISTINCT district FROM listings "
                    "WHERE district IS NOT NULL AND district != '' ORDER BY district"
                ).fetchall()
            for row in rows:
                district = row["district"]
                self.district_combo.addItem(district, userData=district)

        # ---- Data loading --------------------------------------------------------

        def reload_data(self) -> None:
            init_database(config.database_path)
            filters = self._active_filters()
            with open_database(config.database_path) as connection:
                repository = ListingReadRepository(connection)
                candidates = repository.load_candidates(config.profile_key)
                ranked = rank_candidates(candidates, filters)
                self._rows = [(item, ranking_row_view_model(item)) for item in ranked]
                visible_ids = {item.candidate.listing_id for item in ranked}
                markers = tuple(
                    marker
                    for marker in build_map_markers(
                        repository.load_map_points(config.profile_key)
                    )
                    if marker.listing_id in visible_ids
                )

            total = len(candidates)
            self.summary_label.setText(
                f"Showing {len(self._rows)} of {total} apartments "
                f"({WORKFLOW_TAB_LABELS[self.current_tab()]})"
            )
            self._populate_table()
            self._map_ready = False
            self.map_view.setHtml(build_leaflet_html(markers))
            self._restore_selection()

        def _populate_table(self) -> None:
            self.table.setRowCount(len(self._rows))
            for row_index, (_, row_view_model) in enumerate(self._rows):
                values = [
                    str(row_view_model.position),
                    row_view_model.score_text,
                    row_view_model.title,
                    row_view_model.price_text,
                    row_view_model.price_per_m2_text,
                    row_view_model.area_text,
                    row_view_model.layout_text,
                    row_view_model.mortgage_text,
                    row_view_model.status_text,
                    row_view_model.flags_text,
                ]
                for column_index, value in enumerate(values):
                    self.table.setItem(row_index, column_index, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()

        def _restore_selection(self) -> None:
            if not self._rows:
                self._current_listing_id = None
                self.detail_text.setPlainText("")
                self.map_detail_text.setPlainText("")
                self.notes_edit.setPlainText("")
                return
            target_row = 0
            if self._current_listing_id is not None:
                for row_index, (ranked, _) in enumerate(self._rows):
                    if ranked.candidate.listing_id == self._current_listing_id:
                        target_row = row_index
                        break
            self.table.selectRow(target_row)

        # ---- Detail and actions --------------------------------------------------

        def _selected_listing_id(self) -> int | None:
            selected_rows = self.table.selectionModel().selectedRows()
            if not selected_rows:
                return None
            return self._rows[selected_rows[0].row()][0].candidate.listing_id

        def show_selected_detail(self) -> None:
            listing_id = self._selected_listing_id()
            if listing_id is None:
                return
            self._current_listing_id = listing_id
            with open_database(config.database_path) as connection:
                detail = ListingReadRepository(connection).load_detail(
                    listing_id,
                    config.profile_key,
                )
                if detail is not None and not detail.is_favorite and not detail.is_rejected:
                    UserStateRepository(connection).mark_viewed(
                        listing_id, datetime.now(timezone.utc).isoformat()
                    )
                    connection.commit()
            if detail is None:
                self.detail_text.setPlainText("")
                self.map_detail_text.setPlainText("")
                self.notes_edit.setPlainText("")
                return
            detail_text = _format_detail_text(detail_view_model(detail))
            self.detail_text.setPlainText(detail_text)
            self.map_detail_text.setPlainText(detail_text)
            self.notes_edit.setPlainText(detail.user_notes or "")
            self.favorite_button.setText("Unfavorite" if detail.is_favorite else "Favorite")
            self.reject_button.setText("Unreject" if detail.is_rejected else "Reject")
            if self._map_ready:
                self.map_view.page().runJavaScript(f"window.focusMarker({listing_id});")

        def _toggle_favorite(self) -> None:
            self._apply_state_change(self._do_toggle_favorite)

        def _toggle_rejected(self) -> None:
            self._apply_state_change(self._do_toggle_rejected)

        def _do_toggle_favorite(self, repository: UserStateRepository, detail) -> None:
            repository.set_favorite(detail.listing_id, not detail.is_favorite)

        def _do_toggle_rejected(self, repository: UserStateRepository, detail) -> None:
            repository.set_rejected(detail.listing_id, not detail.is_rejected)

        def _apply_state_change(self, action) -> None:
            listing_id = self._selected_listing_id()
            if listing_id is None:
                return
            with open_database(config.database_path) as connection:
                detail = ListingReadRepository(connection).load_detail(
                    listing_id, config.profile_key
                )
                if detail is None:
                    return
                action(UserStateRepository(connection), detail)
                connection.commit()
            self.reload_data()

        def _save_notes(self) -> None:
            listing_id = self._selected_listing_id()
            if listing_id is None:
                return
            notes = self.notes_edit.toPlainText().strip() or None
            with open_database(config.database_path) as connection:
                UserStateRepository(connection).update_notes(listing_id, notes)
                connection.commit()
            QMessageBox.information(self, "Notes saved", "Your notes were saved.")

        def _open_in_browser(self) -> None:
            listing_id = self._selected_listing_id()
            if listing_id is None:
                return
            with open_database(config.database_path) as connection:
                detail = ListingReadRepository(connection).load_detail(
                    listing_id, config.profile_key
                )
            if detail is not None and detail.ss_url:
                webbrowser.open(detail.ss_url)

        def select_listing(self, listing_id: int) -> None:
            for row_index, (ranked, _) in enumerate(self._rows):
                if ranked.candidate.listing_id == listing_id:
                    self.table.selectRow(row_index)
                    return

        def _map_loaded(self, loaded: bool) -> None:
            self._map_ready = loaded
            if loaded:
                self.show_selected_detail()

    return FlatSearcherWindow()


def _format_detail_text(view_model: DetailViewModel) -> str:
    sections = [
        view_model.title,
        f"Original listing: {view_model.ss_url}",
        "",
        _section("Top", view_model.top_lines),
        _section("Layout", view_model.layout_lines),
        _section("Mortgage", view_model.mortgage_lines),
        _section("Location", view_model.location_lines),
        _section("History", view_model.history_lines),
        "",
        "Original Listing Text",
        view_model.original_listing_text,
    ]
    return "\n".join(section for section in sections if section is not None)


def _section(title: str, lines: tuple[str, ...]) -> str:
    return "\n".join((f"{title}:", *(f"  {line}" for line in lines), ""))
