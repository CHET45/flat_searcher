from unittest import TestCase

from flat_searcher.mapping import MapMarker, MarkerVisualState
from flat_searcher.ui.map_html import build_leaflet_html


class LeafletHTMLTests(TestCase):
    def test_html_contains_markers_web_channel_and_focus_function(self) -> None:
        html = build_leaflet_html(
            (
                MapMarker(
                    listing_id=12,
                    latitude=56.95,
                    longitude=24.1,
                    visual_state=MarkerVisualState.FAVORITE,
                    score_bucket="high",
                ),
            )
        )

        self.assertIn("leaflet@1.9.4", html)
        self.assertIn("qtwebchannel/qwebchannel.js", html)
        self.assertIn('"listing_id":12', html)
        self.assertIn('"visual_state":"favorite"', html)
        self.assertIn("function focusMarker", html)
        self.assertIn("bridge.markerSelected", html)
