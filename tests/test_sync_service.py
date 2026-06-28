import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.db import ListingRepository, open_database
from flat_searcher.db.bootstrap import init_database
from flat_searcher.scraper.http_client import FetchResult
from flat_searcher.services.sync import ListingSyncService


START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"


class FakeHttpClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def fetch_text(self, url: str) -> FetchResult:
        self.requested_urls.append(url)
        return FetchResult(url=url, text=self.pages[url])


class ListingSyncServiceTests(TestCase):
    def test_sync_follows_list_pagination_until_limit_or_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            init_database(database_path)
            client = FakeHttpClient(
                {
                    START_URL: _list_page(
                        page=1,
                        listing_id="1001",
                        detail_path="/msg/lv/one.html",
                        next_path="/lv/real-estate/flats/riga/all/sell/page2.html",
                    ),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html": _list_page(
                        page=2,
                        listing_id="1002",
                        detail_path="/msg/lv/two.html",
                    ),
                    "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                    "https://www.ss.com/msg/lv/two.html": _detail_page("Second apartment."),
                }
            )

            result = ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=client,
            ).sync()

            with open_database(database_path) as connection:
                count = ListingRepository(connection).count_listings()

        self.assertEqual(result.seen_count, 2)
        self.assertEqual(result.stored_count, 2)
        self.assertEqual(count, 2)
        self.assertIn(
            "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html",
            client.requested_urls,
        )


def _list_page(
    page: int,
    listing_id: str,
    detail_path: str,
    next_path: str | None = None,
    price: str = "100 000 €",
) -> str:
    next_link = (
        f'<a name="nav_id" rel="next" class="navi" href="{next_path}">Nākamie</a>'
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


def _detail_page(description: str) -> str:
    return f'<html><body><div id="msg_div_msg">{description}</div></body></html>'


class ListingSyncProgressTests(TestCase):
    def test_sync_discovers_total_before_downloading_details_and_reports_each_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            progress_events: list[dict[str, object]] = []
            client = FakeHttpClient(
                {
                    START_URL: _list_page(
                        page=1,
                        listing_id="2001",
                        detail_path="/msg/lv/one.html",
                        next_path="/lv/real-estate/flats/riga/all/sell/page2.html",
                    ),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html": _list_page(
                        page=2,
                        listing_id="2002",
                        detail_path="/msg/lv/two.html",
                        next_path="/lv/real-estate/flats/riga/all/sell/page3.html",
                    ),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page3.html": _list_page(
                        page=3,
                        listing_id="2003",
                        detail_path="/msg/lv/three.html",
                    ),
                    "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                    "https://www.ss.com/msg/lv/two.html": _detail_page("Second apartment."),
                    "https://www.ss.com/msg/lv/three.html": _detail_page("Third apartment."),
                }
            )

            result = ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=client,
            ).sync(progress_callback=progress_events.append)

        self.assertEqual(result.seen_count, 3)
        self.assertEqual(result.stored_count, 3)
        self.assertIn(
            {"stage": "sync", "current": 0, "total": 3, "pages": 3},
            progress_events,
        )
        self.assertEqual(
            [event["current"] for event in progress_events if event["stage"] == "sync"],
            [0, 1, 2, 3],
        )


    def test_sync_reports_list_page_fetch_before_first_network_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            progress_events: list[dict[str, object]] = []
            client = FakeHttpClient(
                {
                    START_URL: _list_page(
                        page=1,
                        listing_id="5001",
                        detail_path="/msg/lv/one.html",
                    ),
                    "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                }
            )

            ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=client,
            ).sync(progress_callback=progress_events.append)

        self.assertEqual(progress_events[0], {"stage": "sync_discover", "pages": 0, "listings": 0})
        self.assertEqual(progress_events[1]["stage"], "sync_list_page")
        self.assertEqual(progress_events[1]["page"], 1)
        self.assertEqual(progress_events[1]["listings"], 0)

    def test_sync_updates_existing_listing_when_ss_values_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            first_client = FakeHttpClient(
                {
                    START_URL: _list_page(
                        page=1,
                        listing_id="3001",
                        detail_path="/msg/lv/one.html",
                    ),
                    "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                }
            )
            ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=first_client,
            ).sync()

            second_client = FakeHttpClient(
                {
                    START_URL: _list_page(
                        page=1,
                        listing_id="3001",
                        detail_path="/msg/lv/one.html",
                        price="110 000 €",
                    ),
                    "https://www.ss.com/msg/lv/one.html": _detail_page("First apartment."),
                }
            )
            result = ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=second_client,
            ).sync()

            with open_database(database_path) as connection:
                row = connection.execute(
                    "SELECT price_eur FROM listings WHERE ss_id = ?",
                    ("3001",),
                ).fetchone()

        self.assertEqual(result.changed_count, 1)
        self.assertEqual(row["price_eur"], 110000)

    def test_sync_uses_sequential_page_fallback_when_navigation_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "flat_searcher.sqlite3"
            detail_urls = [f"https://www.ss.com/msg/lv/{i}.html" for i in range(61)]
            client = FakeHttpClient(
                {
                    START_URL: _list_page_many(0, 30, next_link=False),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page2.html": _list_page_many(30, 30, next_link=False),
                    "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page3.html": _list_page_many(60, 1, next_link=False),
                    **{url: _detail_page(f"Apartment {i}") for i, url in enumerate(detail_urls)},
                }
            )

            result = ListingSyncService(
                database_path=database_path,
                start_url=START_URL,
                http_client=client,
            ).sync()

        self.assertEqual(result.seen_count, 61)
        self.assertIn(
            "https://www.ss.com/lv/real-estate/flats/riga/all/sell/page3.html",
            client.requested_urls,
        )


def _list_page_many(start: int, count: int, next_link: bool = True) -> str:
    rows = []
    for offset in range(count):
        listing_id = 4000 + start + offset
        rows.append(
            f'''
            <tr id="tr_{listing_id}">
              <td></td>
              <td><a href="/msg/lv/{start + offset}.html"></a></td>
              <td><a class="am" href="/msg/lv/{start + offset}.html">Apartment {listing_id}</a></td>
              <td>Centrs<br>Testa {listing_id}</td>
              <td>2</td>
              <td>45</td>
              <td>2/5</td>
              <td>Renov.</td>
              <td>100 000 €</td>
            </tr>
            '''
        )
    link = '<a name="nav_id" rel="next" class="navi" href="page2.html">Nākamie</a>' if next_link else ""
    return f"<html><body><table>{''.join(rows)}</table><div>{link}</div></body></html>"
