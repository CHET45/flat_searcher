import importlib.util
from unittest import TestCase

from flat_searcher.presentation import DetailViewModel
from flat_searcher.ui import DesktopUIConfig, UIDependencyError
from flat_searcher.ui.app import _format_detail_text

_HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None


def _sample_view_model() -> DetailViewModel:
    return DetailViewModel(
        listing_id=1,
        title="Teika 2-room apartment",
        top_lines=("Price: 90 000 EUR", "Area: 50 m2"),
        layout_lines=("AI-effective private rooms: 2",),
        mortgage_lines=("Mortgage risk: Low",),
        location_lines=("Address precision: exact_house",),
        history_lines=("Snapshots: 1",),
        original_listing_text="Nice apartment near the park.",
        ss_url="https://www.ss.com/msg/1.html",
    )


class DetailTextFormattingTests(TestCase):
    def test_format_detail_text_includes_sections_and_original_text(self) -> None:
        text = _format_detail_text(_sample_view_model())

        self.assertIn("Teika 2-room apartment", text)
        self.assertIn("Original listing: https://www.ss.com/msg/1.html", text)
        self.assertIn("Layout:", text)
        self.assertIn("Mortgage:", text)
        self.assertIn("Original Listing Text", text)
        self.assertIn("Nice apartment near the park.", text)

    def test_format_detail_text_never_leaks_raw_ai_fields(self) -> None:
        text = _format_detail_text(_sample_view_model())

        for forbidden in ("pass1_output", "pass2_output", "prompt", "json"):
            self.assertNotIn(forbidden, text.lower())


class UIDependencyTests(TestCase):
    def test_run_desktop_app_requires_pyside6_when_missing(self) -> None:
        if _HAS_PYSIDE6:
            self.skipTest("PySide6 is installed; dependency guard cannot be exercised.")
        from flat_searcher.ui import run_desktop_app

        with self.assertRaises(UIDependencyError):
            run_desktop_app(DesktopUIConfig(database_path="ignored.sqlite3"))
