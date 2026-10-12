import threading
import time
from unittest import TestCase

from flat_searcher.scraper.crawl import SSCrawler
from flat_searcher.scraper.http_client import FetchError, FetchResult

START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"
PAGE_2_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html"


class FakeHttpClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def fetch_text(self, url: str) -> FetchResult:
        self.requested_urls.append(url)
        if url not in self.pages:
            raise FetchError(f"Missing page: {url}")
        return FetchResult(url=url, text=self.pages[url])


class CrawlPaginationTests(TestCase):
    def test_crawl_follows_pagination_and_reports_a_complete_crawl(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page(
                    page=1,
                    listing_id="1001",
                    detail_path="/msg/lv/one.html",
                    next_path="/lv/real-estate/flats/riga/all/sell/page2.html",
                ),
                PAGE_2_URL: _list_page(
                    page=2,
                    listing_id="1002",
                    detail_path="/msg/lv/two.html",
                ),
                "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                "https://www.ss.com/msg/lv/two.html": _detail_page("Second apartment."),
            }
        )

        result = SSCrawler(start_url=START_URL, http_client=client).crawl()

        self.assertTrue(result.complete)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.failed_page_count, 0)
        self.assertEqual(result.seen_ss_ids, frozenset({"1001", "1002"}))
        self.assertEqual([payload.ss_id for payload in result.payloads], ["1001", "1002"])
        self.assertIn(PAGE_2_URL, client.requested_urls)

    def test_crawl_uses_sequential_page_fallback_when_navigation_is_missing(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page_many(0, 30),
                PAGE_2_URL: _list_page_many(30, 2),
                **{
                    f"https://www.ss.com/msg/lv/{index}.html": _detail_page(f"Apartment {index}")
                    for index in range(32)
                },
            }
        )

        result = SSCrawler(start_url=START_URL, http_client=client).crawl()

        self.assertTrue(result.complete)
        self.assertEqual(len(result.payloads), 32)
        self.assertIn(PAGE_2_URL, client.requested_urls)

    def test_crawl_limit_marks_the_crawl_incomplete(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page_many(0, 30),
                **{
                    f"https://www.ss.com/msg/lv/{index}.html": _detail_page(f"Apartment {index}")
                    for index in range(30)
                },
            }
        )

        result = SSCrawler(start_url=START_URL, http_client=client).crawl(limit=2)

        self.assertTrue(result.truncated)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.payloads), 2)


class CrawlBeyondThePagerWindowTests(TestCase):
    LAST_PAGE = 13

    def test_every_page_is_reached_when_page_one_does_not_wrap_to_the_last(self) -> None:
        for workers in (1, 4):
            for arrow in (True, False):
                with self.subTest(workers=workers, arrow=arrow):
                    client = FakeHttpClient(_windowed_site(self.LAST_PAGE, arrow=arrow))

                    result = SSCrawler(START_URL, client, list_fetch_workers=workers).crawl()

                    self.assertTrue(result.complete)
                    self.assertEqual(result.page_count, self.LAST_PAGE)
                    self.assertEqual(
                        result.seen_ss_ids, frozenset(str(5000 + n) for n in range(1, self.LAST_PAGE + 1))
                    )

    def test_a_page_past_the_window_that_fails_leaves_the_crawl_incomplete(self) -> None:
        for workers in (1, 4):
            for missing in (5, 9, 10, self.LAST_PAGE):
                with self.subTest(workers=workers, missing=missing):
                    pages = _windowed_site(self.LAST_PAGE, arrow=True)
                    del pages[_page_url(missing)]
                    client = FakeHttpClient(pages)

                    result = SSCrawler(START_URL, client, list_fetch_workers=workers).crawl()

                    self.assertFalse(result.complete)
                    self.assertNotIn(str(5000 + missing), result.seen_ss_ids)
                    list_requests = [url for url in client.requested_urls if "/msg/" not in url]
                    self.assertEqual(len(list_requests), len(set(list_requests)))


    def test_fast_discovery_ends_at_a_full_last_page_without_probing_past_it(self) -> None:
        nav = '<div><a name="nav_id" rel="next" class="navi" href="page2.html">2</a></div>'
        client = FakeHttpClient(
            {
                START_URL: _list_page_many(0, 30).replace("</table>", f"</table>{nav}"),
                PAGE_2_URL: _list_page_many(30, 30),
                **{f"https://www.ss.com/msg/lv/{index}.html": _detail_page("Flat.") for index in range(60)},
            }
        )

        result = SSCrawler(START_URL, client, list_fetch_workers=4).crawl()

        self.assertTrue(result.complete)
        self.assertEqual(result.seen_count, 60)
        self.assertEqual([url for url in client.requested_urls if "/msg/" not in url], [START_URL, PAGE_2_URL])


class CrawlFailureTests(TestCase):
    def test_failed_list_page_marks_the_crawl_incomplete_and_keeps_parsed_listings(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page(
                    page=1,
                    listing_id="2001",
                    detail_path="/msg/lv/one.html",
                    next_path="/lv/real-estate/flats/riga/all/sell/page2.html",
                ),
                "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
            }
        )

        result = SSCrawler(start_url=START_URL, http_client=client).crawl()

        self.assertEqual(result.failed_page_count, 1)
        self.assertFalse(result.complete)
        self.assertEqual(result.seen_ss_ids, frozenset({"2001"}))
        self.assertEqual([payload.ss_id for payload in result.payloads], ["2001"])

    def test_failed_list_page_in_concurrent_discovery_marks_the_crawl_incomplete(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page(
                    page=1,
                    listing_id="2101",
                    detail_path="/msg/lv/one.html",
                    next_path="/lv/real-estate/flats/riga/all/sell/page2.html",
                ),
                "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
            }
        )

        result = SSCrawler(
            start_url=START_URL,
            http_client=client,
            list_fetch_workers=2,
        ).crawl()

        self.assertEqual(result.failed_page_count, 1)
        self.assertFalse(result.complete)
        self.assertEqual([payload.ss_id for payload in result.payloads], ["2101"])

    def test_failed_detail_page_drops_the_payload_but_keeps_the_listing_seen(self) -> None:
        client = FakeHttpClient(
            {
                START_URL: _list_page_many(0, 2),
                "https://www.ss.com/msg/lv/0.html": _detail_page("First apartment."),
            }
        )

        result = SSCrawler(start_url=START_URL, http_client=client).crawl()

        self.assertTrue(result.complete)
        self.assertEqual(result.failed_detail_count, 1)
        self.assertEqual(len(result.payloads), 1)
        self.assertEqual(result.seen_ss_ids, frozenset({"4000", "4001"}))

    def test_unreadable_first_list_page_raises(self) -> None:
        crawler = SSCrawler(start_url=START_URL, http_client=FakeHttpClient({}))

        with self.assertRaises(FetchError):
            crawler.crawl()


class SlowHttpClient(FakeHttpClient):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(pages)
        self._lock = threading.Lock()

    def fetch_text(self, url: str) -> FetchResult:
        time.sleep(0.02)
        with self._lock:
            return super().fetch_text(url)


class Halt(BaseException):
    pass


class CrawlProgressTests(TestCase):
    def test_progress_counts_list_pages_then_listings(self) -> None:
        nav = "".join(
            f'<a name="nav_id" class="navi" href="/lv/real-estate/flats/riga/all/sell/page{n}.html">{n}</a>'
            for n in range(2, 5)
        )
        pages = {
            START_URL: _list_page_many(0, 2).replace("</table>", f"</table><div>{nav}</div>"),
            **{
                f"https://www.ss.com/lv/real-estate/flats/riga/all/sell/page{n}.html": _list_page_many(n * 10, 1)
                for n in range(2, 5)
            },
            **{f"https://www.ss.com/msg/lv/{i}.html": _detail_page("Flat.") for i in (0, 1, 20, 30, 40)},
        }
        events: list[tuple[str, int, int]] = []

        result = SSCrawler(START_URL, FakeHttpClient(pages), list_fetch_workers=2, detail_fetch_workers=2).crawl(
            progress=lambda phase, done, total: events.append((phase, done, total))
        )

        self.assertTrue(result.complete)
        self.assertEqual(
            [event for event in events if event[0] == "list pages"],
            [("list pages", 1, 4), ("list pages", 2, 4), ("list pages", 3, 4), ("list pages", 4, 4)],
        )
        self.assertEqual(
            [event for event in events if event[0] == "listings"], [("listings", n, 5) for n in range(1, 6)]
        )

    def test_a_raising_progress_ends_the_crawl_without_fetching_the_rest(self) -> None:
        pages = {
            START_URL: _list_page_many(0, 29),
            **{f"https://www.ss.com/msg/lv/{i}.html": _detail_page("Flat.") for i in range(29)},
        }
        client = SlowHttpClient(pages)

        def progress(phase: str, done: int, total: int) -> None:
            if phase == "listings" and done == 2:
                raise Halt

        with self.assertRaises(Halt):
            SSCrawler(START_URL, client, detail_fetch_workers=2).crawl(progress=progress)
        time.sleep(0.1)

        details = [url for url in client.requested_urls if "/msg/" in url]
        self.assertLess(len(details), 10)


def _list_page(
    page: int,
    listing_id: str,
    detail_path: str,
    next_path: str | None = None,
    price: str = "100 000 €",
) -> str:
    next_link = (
        f'<a name="nav_id" rel="next" class="navi" href="{next_path}">Nakamie</a>'
        if next_path
        else ""
    )
    return f"""
        <html><body>
          <table>
            <tr id="tr_{listing_id}">
              <td></td>
              <td><a href="{detail_path}"></a></td>
              <td><a class="am" href="{detail_path}">Apartment {listing_id}</a></td>
              <td>Centrs<br>Testa {page}</td>
              <td>2</td>
              <td>45</td>
              <td>2/5</td>
              <td>Renov.</td>
              <td>{price}</td>
            </tr>
          </table>
          <div><button class="navia">{page}</button>{next_link}</div>
        </body></html>
    """


def _list_page_many(start: int, count: int) -> str:
    rows = "".join(
        f"""
            <tr id="tr_{4000 + start + offset}">
              <td></td>
              <td><a href="/msg/lv/{start + offset}.html"></a></td>
              <td><a class="am" href="/msg/lv/{start + offset}.html">Apartment</a></td>
              <td>Centrs<br>Testa {start + offset}</td>
              <td>2</td>
              <td>45</td>
              <td>2/5</td>
              <td>Renov.</td>
              <td>100 000 €</td>
            </tr>
        """
        for offset in range(count)
    )
    return f"<html><body><table>{rows}</table></body></html>"


def _page_url(page: int) -> str:
    return START_URL if page == 1 else f"{START_URL}page{page}.html"


def _windowed_site(last_page: int, arrow: bool) -> dict[str, str]:
    """SS-style pager: numbers up to eight pages ahead, every forward link `rel="next"`,
    and no wrap-around from page 1 to the last page."""
    pages: dict[str, str] = {}
    for page in range(1, last_page + 1):
        links = []
        if page > 1:
            links.append(f'<a name="nav_id" rel="prev" class="navi" href="{_page_url(page - 1)}">Iepriekšējie</a>')
        for number in range(max(1, page - 4), min(last_page, page + 8) + 1):
            if number == page:
                links.append(f'<button class="navia">{page}</button>')
            else:
                rel = ' rel="next"' if number > page else ""
                links.append(f'<a name="nav_id"{rel} class="navi" href="{_page_url(number)}">{number}</a>')
        if arrow and page < last_page:
            links.append(f'<a name="nav_id" rel="next" class="navi" href="{_page_url(page + 1)}">Nākamie</a>')
        listing = _list_page(page, str(5000 + page), f"/msg/lv/{5000 + page}.html")
        pages[_page_url(page)] = listing.replace(f'<button class="navia">{page}</button>', "".join(links))
        pages[f"https://www.ss.com/msg/lv/{5000 + page}.html"] = _detail_page("Flat.")
    return pages


def _detail_page(description: str) -> str:
    return f'<html><body><div id="msg_div_msg">{description}</div></body></html>'
