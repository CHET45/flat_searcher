"""Store-agnostic SS.com crawl."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

from flat_searcher.models import ListingDetail, ListingPayload, ListingSummary
from flat_searcher.scraper.http_client import FetchError, FetchResult
from flat_searcher.scraper.ss import (
    SSDetailParser,
    SSListParser,
    merge_listing,
    page_number_from_url,
)

MAX_LIST_PAGES = 500
FULL_LIST_PAGE_ROWS = 30

logger = logging.getLogger(__name__)


class TextFetcher(Protocol):
    def fetch_text(self, url: str) -> FetchResult: ...


@dataclass(frozen=True)
class CrawlResult:
    """Everything one crawl learned, with no opinion about where it is stored."""

    payloads: tuple[ListingPayload, ...]
    seen_ss_ids: frozenset[str]
    page_count: int
    failed_page_count: int
    failed_detail_count: int
    truncated: bool

    @property
    def complete(self) -> bool:
        return self.failed_page_count == 0 and not self.truncated

    @property
    def seen_count(self) -> int:
        return len(self.seen_ss_ids)


@dataclass(frozen=True)
class _Discovery:
    summaries: tuple[ListingSummary, ...]
    page_count: int
    failed_page_count: int
    truncated: bool


class SSCrawler:
    """Walks SS list pages, fetches every detail page, and returns parsed payloads.

    `seen_ss_ids` - not `payloads` - is the presence set: a listing whose detail page
    failed is still known to exist. Callers must treat absence as meaningful only when
    `CrawlResult.complete` is true.
    """

    def __init__(
        self,
        start_url: str,
        http_client: TextFetcher,
        list_fetch_workers: int = 1,
        detail_fetch_workers: int = 1,
    ) -> None:
        self.start_url = start_url
        self.http_client = http_client
        self.list_fetch_workers = max(1, int(list_fetch_workers))
        self.detail_fetch_workers = max(1, int(detail_fetch_workers))
        self.list_parser = SSListParser()
        self.detail_parser = SSDetailParser()

    def crawl(self, limit: int | None = None) -> CrawlResult:
        discovery = self._discover_summaries(limit)
        details_by_ss_id: dict[str, ListingDetail] = {}
        failed_detail_count = 0

        for summary, detail in self._iter_details(discovery.summaries):
            if detail is None:
                failed_detail_count += 1
                continue
            details_by_ss_id[summary.ss_id] = detail

        payloads = tuple(
            merge_listing(summary, details_by_ss_id[summary.ss_id])
            for summary in discovery.summaries
            if summary.ss_id in details_by_ss_id
        )
        result = CrawlResult(
            payloads=payloads,
            seen_ss_ids=frozenset(summary.ss_id for summary in discovery.summaries),
            page_count=discovery.page_count,
            failed_page_count=discovery.failed_page_count,
            failed_detail_count=failed_detail_count,
            truncated=discovery.truncated,
        )
        logger.info(
            "crawl finished: pages=%s seen=%s parsed=%s failed_pages=%s "
            "failed_details=%s complete=%s",
            result.page_count,
            result.seen_count,
            len(result.payloads),
            result.failed_page_count,
            result.failed_detail_count,
            result.complete,
        )
        return result

    def _discover_summaries(self, limit: int | None) -> _Discovery:
        summaries_by_ss_id: OrderedDict[str, ListingSummary] = OrderedDict()
        visited_pages: set[str] = set()
        next_url: str | None = self.start_url
        page_count = 0
        failed_page_count = 0
        truncated = False

        while next_url is not None and (limit is None or len(summaries_by_ss_id) < limit):
            if next_url in visited_pages:
                break
            if page_count >= MAX_LIST_PAGES:
                raise FetchError(
                    f"Stopped after {MAX_LIST_PAGES} SS list pages to avoid an infinite crawl."
                )

            page_number = page_count + 1
            visited_pages.add(next_url)
            try:
                list_page = self.http_client.fetch_text(next_url)
            except FetchError as error:
                if page_count == 0:
                    raise FetchError(f"Failed while reading SS list page 1: {error}") from error
                logger.warning(
                    "crawl list page %s failed, crawl is incomplete: %s", page_number, error
                )
                failed_page_count += 1
                break

            visited_pages.add(list_page.url)
            page_count += 1
            page_summaries = self.list_parser.parse(list_page.text, list_page.url)
            for summary in page_summaries:
                if limit is not None and len(summaries_by_ss_id) >= limit:
                    truncated = True
                    break
                summaries_by_ss_id.setdefault(summary.ss_id, summary)

            total_pages = self.list_parser.max_navigation_page(list_page.text, list_page.url)
            if limit is None and self.list_fetch_workers > 1 and total_pages > page_count:
                remaining_pages = range(page_count + 1, min(total_pages, MAX_LIST_PAGES) + 1)
                fetched, failed = self._fetch_list_pages_concurrently(
                    [(page, _list_page_url(list_page.url, page)) for page in remaining_pages]
                )
                failed_page_count += failed
                for page in sorted(fetched):
                    for summary in fetched[page]:
                        summaries_by_ss_id.setdefault(summary.ss_id, summary)
                page_count += len(fetched)
                break

            if limit is not None and len(summaries_by_ss_id) >= limit:
                truncated = True
                break

            next_candidate = self.list_parser.next_page_url(
                list_page.text, list_page.url
            ) or _sequential_next_page_url(list_page.url, len(page_summaries), visited_pages)
            next_url = None if next_candidate in visited_pages else next_candidate

        return _Discovery(
            summaries=tuple(summaries_by_ss_id.values()),
            page_count=page_count,
            failed_page_count=failed_page_count,
            truncated=truncated,
        )

    def _fetch_list_pages_concurrently(
        self,
        page_urls: Sequence[tuple[int, str]],
    ) -> tuple[dict[int, list[ListingSummary]], int]:
        fetched: dict[int, list[ListingSummary]] = {}
        failed_page_count = 0
        with ThreadPoolExecutor(max_workers=self.list_fetch_workers) as executor:
            futures = {
                executor.submit(self._fetch_list_page, url): page_number
                for page_number, url in page_urls
            }
            for future in as_completed(futures):
                summaries = future.result()
                if summaries is None:
                    failed_page_count += 1
                    continue
                fetched[futures[future]] = summaries
        return fetched, failed_page_count

    def _fetch_list_page(self, url: str) -> list[ListingSummary] | None:
        try:
            list_page = self.http_client.fetch_text(url)
        except FetchError as error:
            logger.warning("crawl list page fetch failed, crawl is incomplete: %s", error)
            return None
        return self.list_parser.parse(list_page.text, list_page.url)

    def _iter_details(
        self,
        summaries: Sequence[ListingSummary],
    ) -> Iterator[tuple[ListingSummary, ListingDetail | None]]:
        if self.detail_fetch_workers <= 1:
            for summary in summaries:
                yield summary, self._fetch_detail(summary)
            return

        with ThreadPoolExecutor(max_workers=self.detail_fetch_workers) as executor:
            futures = {
                executor.submit(self._fetch_detail, summary): summary for summary in summaries
            }
            for future in as_completed(futures):
                yield futures[future], future.result()

    def _fetch_detail(self, summary: ListingSummary) -> ListingDetail | None:
        try:
            detail_page = self.http_client.fetch_text(summary.ss_url)
        except FetchError as error:
            logger.warning("crawl detail fetch failed: ss_id=%s: %s", summary.ss_id, error)
            return None
        return self.detail_parser.parse(detail_page.text)


def _list_page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    return urljoin(base_url, f"page{page_number}.html")


def _sequential_next_page_url(
    current_url: str,
    page_listing_count: int,
    visited_pages: set[str],
) -> str | None:
    """SS sometimes hides the next page behind an ellipsis or a localized label.

    Only advance from a page that looks full, so a malformed navigation block cannot
    make the crawl probe forever past the real last page.
    """
    if page_listing_count < FULL_LIST_PAGE_ROWS:
        return None
    candidate = urljoin(current_url, f"page{page_number_from_url(current_url) + 1}.html")
    return None if candidate in visited_pages else candidate
