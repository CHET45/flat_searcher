"""Listing synchronization service."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.repository import ListingRepository, open_database
from flat_searcher.models import ListingSummary
from flat_searcher.scraper import SSDetailParser, SSListParser, merge_listing
from flat_searcher.scraper.http_client import FetchError, HttpTextClient

SyncProgressCallback = Callable[[dict[str, object]], None]
MAX_LIST_PAGES = 500
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    seen_count: int
    stored_count: int
    new_count: int
    changed_count: int
    inactive_count: int
    failed_count: int


@dataclass(frozen=True)
class ListingDiscoveryResult:
    summaries: tuple[ListingSummary, ...]
    page_count: int


class ListingSyncService:
    def __init__(
        self,
        database_path: Path,
        start_url: str,
        http_client: HttpTextClient,
        list_fetch_workers: int = 1,
        detail_fetch_workers: int = 1,
    ) -> None:
        self.database_path = database_path
        self.start_url = start_url
        self.http_client = http_client
        self.list_fetch_workers = max(1, int(list_fetch_workers))
        self.detail_fetch_workers = max(1, int(detail_fetch_workers))
        self.list_parser = SSListParser()
        self.detail_parser = SSDetailParser()

    def sync(
        self,
        limit: int | None = None,
        mark_missing_inactive: bool = False,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        logger.info(
            "sync-listings start: database=%s start_url=%s limit=%s mark_missing_inactive=%s",
            self.database_path,
            self.start_url,
            limit,
            mark_missing_inactive,
        )
        init_database(self.database_path)
        logger.info("sync-listings database initialized: %s", self.database_path)
        started_at = _now()

        with open_database(self.database_path) as connection:
            repository = ListingRepository(connection)
            logger.info("sync-listings creating app run")
            app_run_id = repository.create_app_run("sync-listings", started_at)
            logger.info("sync-listings app run created: id=%s", app_run_id)
            try:
                result = self._sync_with_repository(
                    repository,
                    app_run_id,
                    limit,
                    mark_missing_inactive,
                    progress_callback,
                )
            except Exception as error:
                logger.exception("sync-listings failed: app_run_id=%s", app_run_id)
                repository.finish_app_run(app_run_id, _now(), "failed", str(error))
                raise
            repository.finish_app_run(
                app_run_id,
                _now(),
                "finished",
                (
                    f"Stored {result.stored_count} listings, "
                    f"{result.new_count} new, {result.changed_count} changed, "
                    f"{result.inactive_count} inactive, {result.failed_count} failed."
                ),
            )
            logger.info(
                "sync-listings finished: seen=%s stored=%s new=%s changed=%s inactive=%s failed=%s",
                result.seen_count,
                result.stored_count,
                result.new_count,
                result.changed_count,
                result.inactive_count,
                result.failed_count,
            )
            return result

    def _sync_with_repository(
        self,
        repository: ListingRepository,
        app_run_id: int,
        limit: int | None,
        mark_missing_inactive: bool,
        progress_callback: SyncProgressCallback | None,
    ) -> SyncResult:
        logger.info("sync-listings discovery phase starting")
        discovery = self._discover_listing_summaries(limit, progress_callback)
        summaries = discovery.summaries
        total = len(summaries)
        logger.info("sync-listings discovery phase finished: pages=%s summaries=%s", discovery.page_count, total)
        seen_ss_ids: set[str] = {summary.ss_id for summary in summaries}

        stored_count = 0
        new_count = 0
        changed_count = 0
        failed_count = 0

        _emit_progress(
            progress_callback,
            {
                "stage": "sync",
                "current": 0,
                "total": total,
                "pages": discovery.page_count,
            },
        )

        detail_items = self._iter_detail_results(summaries)
        for current, summary, detail, failed in detail_items:
            if failed:
                failed_count += 1

            upsert_result = repository.upsert_listing(
                merge_listing(summary, detail),
                app_run_id=app_run_id,
                checked_at=_now(),
            )
            stored_count += 1
            if upsert_result.is_new:
                new_count += 1
            if upsert_result.change_events:
                changed_count += 1

            _emit_progress(
                progress_callback,
                {
                    "stage": "sync",
                    "current": current,
                    "total": total,
                    "pages": discovery.page_count,
                    "new": new_count,
                    "changed": changed_count,
                    "failed": failed_count,
                    "listingId": summary.ss_id,
                    "aiQueued": new_count + changed_count,
                },
            )

        inactive_count = 0
        if mark_missing_inactive:
            logger.info("sync-listings marking missing active listings inactive: seen=%s", len(seen_ss_ids))
            inactive_count = repository.mark_missing_inactive(seen_ss_ids, app_run_id, _now())
            logger.info("sync-listings marked inactive: count=%s", inactive_count)

        return SyncResult(
            seen_count=total,
            stored_count=stored_count,
            new_count=new_count,
            changed_count=changed_count,
            inactive_count=inactive_count,
            failed_count=failed_count,
        )

    def _discover_listing_summaries(
        self,
        limit: int | None,
        progress_callback: SyncProgressCallback | None,
    ) -> ListingDiscoveryResult:
        summaries_by_ss_id: OrderedDict[str, ListingSummary] = OrderedDict()
        next_url: str | None = self.start_url
        visited_pages: set[str] = set()
        page_count = 0

        logger.info("sync-listings discovery initialized: start_url=%s limit=%s", self.start_url, limit)
        _emit_progress(
            progress_callback,
            {"stage": "sync_discover", "pages": 0, "listings": 0},
        )

        while next_url is not None and (limit is None or len(summaries_by_ss_id) < limit):
            if next_url in visited_pages:
                break
            if page_count >= MAX_LIST_PAGES:
                raise FetchError(
                    f"Stopped after {MAX_LIST_PAGES} SS list pages to avoid an infinite crawl."
                )

            page_number = page_count + 1
            logger.debug("sync-listings reading list page %s: %s", page_number, next_url)
            _emit_progress(
                progress_callback,
                {
                    "stage": "sync_list_page",
                    "page": page_number,
                    "listings": len(summaries_by_ss_id),
                    "url": next_url,
                },
            )
            visited_pages.add(next_url)

            try:
                list_page = self.http_client.fetch_text(next_url)
            except FetchError as error:
                logger.exception("sync-listings failed reading list page %s: %s", page_number, next_url)
                raise FetchError(f"Failed while reading SS list page {page_number}: {error}") from error

            visited_pages.add(list_page.url)
            page_count += 1
            page_summaries = self.list_parser.parse(list_page.text, list_page.url)
            logger.debug(
                "sync-listings parsed list page %s: requested=%s final=%s parsed=%s total_unique=%s",
                page_number,
                next_url,
                list_page.url,
                len(page_summaries),
                len(summaries_by_ss_id),
            )
            for summary in page_summaries:
                if limit is not None and len(summaries_by_ss_id) >= limit:
                    break
                summaries_by_ss_id.setdefault(summary.ss_id, summary)

            total_pages = self.list_parser.max_navigation_page(list_page.text, list_page.url)
            _emit_progress(
                progress_callback,
                {
                    "stage": "sync_discover",
                    "pages": page_count,
                    "totalPages": total_pages if total_pages > page_count else None,
                    "listings": len(summaries_by_ss_id),
                    "lastPageCount": len(page_summaries),
                },
            )

            if limit is None and self.list_fetch_workers > 1 and total_pages > page_count:
                logger.info(
                    "sync-listings fast discovery: fetching pages %s..%s with %s workers",
                    page_count + 1,
                    total_pages,
                    self.list_fetch_workers,
                )
                remaining_urls = [
                    (page, _list_page_url(list_page.url, page))
                    for page in range(page_count + 1, min(total_pages, MAX_LIST_PAGES) + 1)
                ]
                page_summaries_by_number = self._fetch_list_pages_concurrently(
                    remaining_urls,
                    progress_callback,
                    initial_pages_done=page_count,
                    initial_listing_count=len(summaries_by_ss_id),
                    total_pages=total_pages,
                )
                for page in sorted(page_summaries_by_number):
                    for summary in page_summaries_by_number[page]:
                        summaries_by_ss_id.setdefault(summary.ss_id, summary)
                page_count = max(page_count, *page_summaries_by_number.keys())
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "sync_discover",
                        "pages": page_count,
                        "totalPages": total_pages,
                        "listings": len(summaries_by_ss_id),
                        "lastPageCount": len(page_summaries_by_number.get(page_count, ())),
                    },
                )
                break

            if limit is not None and len(summaries_by_ss_id) >= limit:
                break
            parsed_next_url = self.list_parser.next_page_url(list_page.text, list_page.url)
            synthetic_next_url = None
            if parsed_next_url is None:
                synthetic_next_url = _synthetic_next_page_url(
                    list_page.url,
                    len(page_summaries),
                    visited_pages,
                )
            next_candidate = parsed_next_url or synthetic_next_url
            logger.debug(
                "sync-listings next list page after page %s: parsed=%s synthetic=%s chosen=%s",
                page_number,
                parsed_next_url,
                synthetic_next_url,
                next_candidate,
            )
            next_url = None if next_candidate in visited_pages else next_candidate

        return ListingDiscoveryResult(tuple(summaries_by_ss_id.values()), page_count)

    def _fetch_list_pages_concurrently(
        self,
        page_urls: list[tuple[int, str]],
        progress_callback: SyncProgressCallback | None,
        initial_pages_done: int,
        initial_listing_count: int,
        total_pages: int,
    ) -> dict[int, list[ListingSummary]]:
        results: dict[int, list[ListingSummary]] = {}
        completed_pages = initial_pages_done
        discovered_count = initial_listing_count
        with ThreadPoolExecutor(max_workers=self.list_fetch_workers) as executor:
            futures = {
                executor.submit(self._fetch_list_page, page_number, url): page_number
                for page_number, url in page_urls
            }
            for future in as_completed(futures):
                page_number = futures[future]
                summaries = future.result()
                results[page_number] = summaries
                completed_pages += 1
                discovered_count += len(summaries)
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "sync_discover",
                        "pages": completed_pages,
                        "totalPages": total_pages,
                        "listings": discovered_count,
                        "lastPageCount": len(summaries),
                    },
                )
        return results

    def _fetch_list_page(self, page_number: int, url: str) -> list[ListingSummary]:
        logger.debug("sync-listings fast list fetch page %s: %s", page_number, url)
        list_page = self._new_http_client().fetch_text(url)
        summaries = self.list_parser.parse(list_page.text, list_page.url)
        logger.debug(
            "sync-listings fast list parsed page %s: requested=%s final=%s parsed=%s",
            page_number,
            url,
            list_page.url,
            len(summaries),
        )
        return summaries

    def _iter_detail_results(
        self,
        summaries: tuple[ListingSummary, ...],
    ):
        if self.detail_fetch_workers <= 1:
            total = len(summaries)
            for index, summary in enumerate(summaries, start=1):
                yield (index, summary, *self._fetch_detail(summary, index, total))
            return

        total = len(summaries)
        completed = 0
        logger.info(
            "sync-listings parallel detail fetch starting: total=%s workers=%s",
            total,
            self.detail_fetch_workers,
        )
        with ThreadPoolExecutor(max_workers=self.detail_fetch_workers) as executor:
            futures = {
                executor.submit(self._fetch_detail, summary, index, total): summary
                for index, summary in enumerate(summaries, start=1)
            }
            for future in as_completed(futures):
                summary = futures[future]
                completed += 1
                detail, failed = future.result()
                yield completed, summary, detail, failed

    def _fetch_detail(
        self,
        summary: ListingSummary,
        index: int,
        total: int,
    ):
        try:
            logger.debug(
                "sync-listings detail fetch %s/%s: ss_id=%s url=%s",
                index,
                total,
                summary.ss_id,
                summary.ss_url,
            )
            detail_page = self._new_http_client().fetch_text(summary.ss_url)
            return self.detail_parser.parse(detail_page.text, detail_page.url), False
        except FetchError:
            logger.exception(
                "sync-listings detail fetch failed: ss_id=%s url=%s",
                summary.ss_id,
                summary.ss_url,
            )
            return None, True

    def _new_http_client(self):
        if isinstance(self.http_client, HttpTextClient):
            return HttpTextClient(
                user_agent=self.http_client.user_agent,
                timeout_seconds=self.http_client.timeout_seconds,
                request_delay_seconds=self.http_client.request_delay_seconds,
            )
        return self.http_client


def _list_page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    return urljoin(base_url, f"page{page_number}.html")

def _synthetic_next_page_url(
    current_url: str,
    page_listing_count: int,
    visited_pages: set[str],
) -> str | None:
    """Return the next sequential SS list page when navigation HTML is incomplete.

    SS sometimes renders the next page behind an ellipsis or a localized label. The
    parser should normally find that link. This fallback is deliberately conservative:
    it only advances when the current page looks like a full SS results page. This
    lets a full crawl continue past malformed navigation but avoids probing forever
    from short final pages.
    """
    if page_listing_count < 30:
        return None
    current_page = _page_number_from_url(current_url)
    candidate = urljoin(current_url, f"page{current_page + 1}.html")
    return None if candidate in visited_pages else candidate


def _page_number_from_url(url: str) -> int:
    import re

    match = re.search(r"/page(\d+)\.html(?:$|[?#])", url)
    if match:
        return int(match.group(1))
    return 1


def _emit_progress(
    progress_callback: SyncProgressCallback | None,
    payload: dict[str, object],
) -> None:
    if progress_callback is not None:
        progress_callback(payload)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
