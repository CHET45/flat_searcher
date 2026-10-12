from pathlib import Path
from unittest import TestCase

from flat_searcher.scraper import SSDetailParser, SSListParser, merge_listing


FIXTURES = Path(__file__).parent / "fixtures" / "ss"
LIST_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"


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

        detail = SSDetailParser().parse(html)

        self.assertEqual(detail.district, "centrs")
        self.assertEqual(detail.detail_fields["Iela"], "Stabu 87")
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
        self.assertIsNotNone(detail.unique_visits)

    def test_summary_and_detail_merge_prefers_detail_values(self) -> None:
        list_html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
        detail_html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")
        summary = SSListParser().parse(list_html, LIST_URL)[0]
        detail = SSDetailParser().parse(detail_html)

        payload = merge_listing(summary, detail)

        self.assertEqual(payload.ss_id, summary.ss_id)
        self.assertEqual(payload.price_eur, 128000)
        self.assertEqual(payload.house_number, "87")
        self.assertGreater(len(payload.image_urls), 10)

    def test_detail_text_is_unescaped_exactly_once(self) -> None:
        html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")
        html = html.replace(
            '<div id="msg_div_msg">',
            '<div id="msg_div_msg">See https://example.lv/x?a=1&amp;copy=2&amp;reg=3 '
            "where &amp;lt; is written as &lt; and Q&amp;A stays.<br>",
            1,
        ).replace("<b>Stabu 87</b>", "<b>Brīvības &amp;amp; Co 87</b>", 1)

        detail = SSDetailParser().parse(html)

        description = detail.description_text or ""
        self.assertIn("https://example.lv/x?a=1&copy=2&reg=3", description)
        self.assertIn("where &lt; is written as < and Q&A stays.", description)
        self.assertEqual(detail.detail_fields["Iela"], "Brīvības &amp; Co 87")
        self.assertEqual(detail.street, "Brīvības &amp; Co")

    def test_list_text_is_unescaped_exactly_once(self) -> None:
        html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
        html = html.replace(
            'class="am" href="/msg/lv/real-estate/flats/riga/plyavnieki/becigg.html">',
            'class="am" href="/msg/lv/real-estate/flats/riga/plyavnieki/becigg.html">&amp;lt;3 &amp;copy ',
            1,
        ).replace("Pļavnieki<br>Grestes 4", "Pļavnieki<br>Grestes &amp;reg 4", 1)

        summary = next(s for s in SSListParser().parse(html, LIST_URL) if s.ss_id == "57778276")

        self.assertTrue((summary.title or "").startswith("&lt;3 &copy Īpašnieks"))
        self.assertEqual(summary.street, "Grestes &reg 4")

    def test_next_page_is_the_nearest_forward_link_wherever_the_arrow_sits(self) -> None:
        html = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
        arrow_start = html.index('<a name="nav_id" rel="next" class="navi" href="/lv/real-estate/flats/riga/all/sell/page2.html">Nākamie')
        arrow_end = html.index("</a>", arrow_start) + len("</a>")
        arrow = html[arrow_start:arrow_end]
        without_arrow = html[:arrow_start] + html[arrow_end:]
        variants = {
            "arrow last": html,
            "arrow first": without_arrow.replace(
                '<button onclick="return false;" class=navia>1</button>',
                f'{arrow} <button onclick="return false;" class=navia>1</button>',
            ),
            "no arrow": without_arrow,
        }

        for name, variant in variants.items():
            with self.subTest(name):
                self.assertEqual(
                    SSListParser().next_page_url(variant, LIST_URL),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html",
                )

    def test_next_page_from_a_middle_page_is_the_nearest_forward_link_in_any_order(self) -> None:
        arrow = '<a name="nav_id" rel="next" class="navi" href="page6.html">Nākamie</a>'
        numbers = """
            <a name="nav_id" rel="prev" class="navi" href="page4.html">Iepriekšējie</a>
            <a name="nav_id" class="navi" href="page3.html">3</a>
            <a name="nav_id" class="navi" href="page4.html">4</a>
            <button class="navia">5</button>
            <a name="nav_id" rel="next" class="navi" href="page7.html">7</a>
            <a name="nav_id" rel="next" class="navi" href="page6.html">6</a>
            <a name="nav_id" rel="next" class="navi" href="page13.html">13</a>
        """

        for name, html in {"arrow first": arrow + numbers, "arrow last": numbers + arrow}.items():
            with self.subTest(name):
                next_url = SSListParser().next_page_url(f"<div>{html}</div>", LIST_URL + "page5.html")

                self.assertEqual(next_url, LIST_URL + "page6.html")

    def test_wrap_around_links_are_never_the_next_page(self) -> None:
        only_page = """
            <a name="nav_id" rel="prev" class="navi" href="/lv/real-estate/flats/riga/all/sell/">Iepriekšējie</a>
            <button class="navia">1</button>
            <a name="nav_id" rel="next" class="navi" href="/lv/real-estate/flats/riga/all/sell/">Nākamie</a>
        """
        pager_without_numbers = """
            <a name="nav_id" rel="prev" class="navi" href="/lv/real-estate/flats/riga/all/sell/page88.html">Iepriekšējie</a>
            <button class="navia">1</button>
        """

        for name, html in {"only page": only_page, "numbers missing": pager_without_numbers}.items():
            with self.subTest(name):
                self.assertIsNone(SSListParser().next_page_url(f"<div>{html}</div>", LIST_URL))

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
