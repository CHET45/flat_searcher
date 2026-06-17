"""Optional PySide6 desktop UI shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.read_repository import ListingReadRepository
from flat_searcher.db.repository import open_database
from flat_searcher.filtering import ListingFilters
from flat_searcher.presentation import DetailViewModel, detail_view_model, ranking_row_view_model
from flat_searcher.ranking import rank_candidates


class UIDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesktopUIConfig:
    database_path: Path
    profile_key: str = "for_living_mortgage"


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
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QPlainTextEdit,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    class FlatSearcherWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Flat Searcher")
            self.resize(1280, 760)
            self._rows = []

            root = QWidget()
            root_layout = QVBoxLayout(root)

            toolbar = QHBoxLayout()
            self.summary_label = QLabel("")
            refresh_button = QPushButton("Refresh")
            refresh_button.clicked.connect(self.reload_data)
            toolbar.addWidget(self.summary_label)
            toolbar.addStretch()
            toolbar.addWidget(refresh_button)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["#", "Score", "Title", "Mortgage", "Status"])
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.itemSelectionChanged.connect(self.show_selected_detail)

            self.detail_text = QPlainTextEdit()
            self.detail_text.setReadOnly(True)

            splitter.addWidget(self.table)
            splitter.addWidget(self.detail_text)
            splitter.setSizes([760, 520])

            root_layout.addLayout(toolbar)
            root_layout.addWidget(splitter)
            self.setCentralWidget(root)
            self.reload_data()

        def reload_data(self) -> None:
            init_database(config.database_path)
            with open_database(config.database_path) as connection:
                repository = ListingReadRepository(connection)
                candidates = repository.load_candidates(config.profile_key)
                ranked = rank_candidates(candidates, ListingFilters())
                self._rows = [(item, ranking_row_view_model(item)) for item in ranked]

            self.summary_label.setText(f"Showing {len(self._rows)} listings")
            self.table.setRowCount(len(self._rows))
            for row_index, (_, row_view_model) in enumerate(self._rows):
                values = [
                    str(row_view_model.position),
                    row_view_model.score_text,
                    row_view_model.title,
                    row_view_model.mortgage_text,
                    row_view_model.status_text,
                ]
                for column_index, value in enumerate(values):
                    self.table.setItem(row_index, column_index, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            if self._rows:
                self.table.selectRow(0)
            else:
                self.detail_text.setPlainText("")

        def show_selected_detail(self) -> None:
            selected_rows = self.table.selectionModel().selectedRows()
            if not selected_rows:
                return
            row_index = selected_rows[0].row()
            listing_id = self._rows[row_index][0].candidate.listing_id
            with open_database(config.database_path) as connection:
                detail = ListingReadRepository(connection).load_detail(
                    listing_id,
                    config.profile_key,
                )
            if detail is None:
                self.detail_text.setPlainText("")
                return
            self.detail_text.setPlainText(_format_detail_text(detail_view_model(detail)))

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
