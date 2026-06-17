"""Listing synchronization service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.repository import ListingRepository, open_database
from flat_searcher.scraper import SSDetailParser, SSListParser, merge_listing
from flat_searcher.scraper.http_client import FetchError, HttpTextClient


@dataclass(frozen=True)
class SyncResult:
    seen_count: int
    stored_count: int
    new_count: int
    changed_count: int
    inactive_count: int
    failed_count: int


class ListingSyncService:
    def __init__(
        self,
        database_path: Path,
        start_url: str,
        http_client: HttpTextClient,
    ) -> None:
        self.database_path = database_path
        self.start_url = start_url
        self.http_client = http_client
        self.list_parser = SSListParser()
        self.detail_parser = SSDetailParser()

    def sync(self, limit: int | None = None, mark_missing_inactive: bool = False) -> SyncResult:
        init_database(self.database_path)
        started_at = _now()

        with open_database(self.database_path) as connection:
            repository = ListingRepository(connection)
            app_run_id = repository.create_app_run("sync-listings", started_at)
            try:
                result = self._sync_with_repository(repository, app_run_id, limit, mark_missing_inactive)
            except Exception as error:
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
            return result

    def _sync_with_repository(
        self,
        repository: ListingRepository,
        app_run_id: int,
        limit: int | None,
        mark_missing_inactive: bool,
    ) -> SyncResult:
        list_page = self.http_client.fetch_text(self.start_url)
        summaries = self.list_parser.parse(list_page.text, list_page.url)
        if limit is not None:
            summaries = summaries[:limit]

        seen_ss_ids: set[str] = set()
        new_count = 0
        changed_count = 0
        failed_count = 0

        for summary in summaries:
            seen_ss_ids.add(summary.ss_id)
            detail = None
            try:
                detail_page = self.http_client.fetch_text(summary.ss_url)
                detail = self.detail_parser.parse(detail_page.text, detail_page.url)
            except FetchError:
                failed_count += 1

            upsert_result = repository.upsert_listing(
                merge_listing(summary, detail),
                app_run_id=app_run_id,
                checked_at=_now(),
            )
            if upsert_result.is_new:
                new_count += 1
            if upsert_result.change_events:
                changed_count += 1

        inactive_count = 0
        if mark_missing_inactive:
            inactive_count = repository.mark_missing_inactive(seen_ss_ids, app_run_id, _now())

        return SyncResult(
            seen_count=len(summaries),
            stored_count=len(summaries),
            new_count=new_count,
            changed_count=changed_count,
            inactive_count=inactive_count,
            failed_count=failed_count,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
