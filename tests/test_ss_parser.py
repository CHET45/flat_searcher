from pathlib import Path
from unittest import TestCase

from flat_searcher.scraper import SSDetailParser, SSListParser, merge_listing


FIXTURES = Path(__file__).parent / "fixtures" / "ss"
LIST_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"
DETAIL_URL = "https://www.ss.com/msg/lv/real-estate/flats/riga/centre/bhodf.html"


class SSParserTests(TestCase):
    def test_list_parser_extracts_listing_rows(self) -> None:
        html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")

        summaries = SSListParser().parse(html, LIST_URL)

        self.assertGreater(len(summaries), 20)
        first = summaries[0]
        self.assertTrue(first.ss_id.isdigit())
        self.assertTrue(first.ss_url.startswith("https://www.ss.com/msg/"))
        self.assertIsNotNone(first.title)
        self.assertIsNotNone(first.district)
        self.assertIsNotNone(first.street)
        self.assertIsNotNone(first.price_eur)

    def test_list_parser_extracts_next_page_url(self) -> None:
        html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")

        next_url = SSListParser().next_page_url(html, LIST_URL)

        self.assertEqual(
            next_url,
            "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html",
        )

    def test_detail_parser_extracts_description_fields_and_images(self) -> None:
        html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")

        detail = SSDetailParser().parse(html, DETAIL_URL)

        self.assertEqual(detail.district, "centrs")
        self.assertEqual(detail.address_raw, "Stabu 87")
        self.assertEqual(detail.street, "Stabu")
        self.assertEqual(detail.house_number, "87")
        self.assertEqual(detail.declared_rooms_ss, 2)
        self.assertEqual(detail.area_m2, 44.0)
        self.assertEqual(detail.floor, 1)
        self.assertEqual(detail.total_floors, 5)
        self.assertEqual(detail.building_series, "Renov.")
        self.assertEqual(detail.building_type, "Ķieģeļu")
        self.assertEqual(detail.price_eur, 128000)
        self.assertAlmostEqual(detail.price_per_m2 or 0, 2909.09)
        self.assertGreater(len(detail.image_urls), 10)
        self.assertIn("Pagalms 87", detail.description_text or "")
        self.assertIsNotNone(detail.listing_date_text)
        self.assertIsNotNone(detail.unique_visits)

    def test_summary_and_detail_merge_prefers_detail_values(self) -> None:
        list_html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
        detail_html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")
        summary = SSListParser().parse(list_html, LIST_URL)[0]
        detail = SSDetailParser().parse(detail_html, DETAIL_URL)

        payload = merge_listing(summary, detail)

        self.assertEqual(payload.ss_id, summary.ss_id)
        self.assertEqual(payload.price_eur, 128000)
        self.assertEqual(payload.address_raw, "Stabu 87")
        self.assertGreater(len(payload.image_urls), 10)

    def test_list_parser_uses_next_numeric_page_hidden_behind_ellipsis(self) -> None:
        html = """
            <html><body>
              <div>
                <a name="nav_id" class="navi" href="page8.html">8</a>
                <button class="navia">9</button>
                <a name="nav_id" class="navi" href="page10.html">..</a>
              </div>
            </body></html>
        """

        next_url = SSListParser().next_page_url(
            html,
            "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page9.html",
        )

        self.assertEqual(
            next_url,
            "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page10.html",
        )
