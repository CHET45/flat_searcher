"""One indexer run: crawl SS.com, diff against the library, write the library back."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from flat_searcher.library.records import (
    ACTIVE,
    REMOVED,
    SUBSTANTIVE,
    Record,
    apply_market_stats,
    build_record,
    classify_change,
    diff_records,
    merge_judgment,
)
from flat_searcher.library.store import LibraryStore
from flat_searcher.scraper.crawl import CrawlResult, SSCrawler, TextFetcher
from flat_searcher.scraper.http_client import HttpTextClient

LIST_FETCH_WORKERS = 4
DETAIL_FETCH_WORKERS = 8

REASON_NEW = "new"
REASON_CHANGED = "changed"
REASON_NEVER_JUDGED = "never_judged"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexerOptions:
    start_url: str
    limit: int | None = None
    request_delay_seconds: float = 1.0
    dry_run: bool = False


@dataclass(frozen=True)
class StageOutcome:
    name: str
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class IndexerResult:
    ok: bool
    stages: tuple[StageOutcome, ...]
    meta: dict[str, Any]


@dataclass
class _RunState:
    now: str
    run_date: str
    library: dict[str, Record] = field(default_factory=dict)
    records: dict[str, Record] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    changes: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    new_ids: set[str] = field(default_factory=set)
    touched_ids: set[str] = field(default_factory=set)
    queue_reasons: dict[str, str] = field(default_factory=dict)
    crawl: CrawlResult | None = None
    deactivation_skipped: bool = False


class IndexerRun:
    def __init__(
        self,
        store: LibraryStore,
        options: IndexerOptions,
        http_client: TextFetcher | None = None,
    ) -> None:
        self.store = store
        self.options = options
        self.http_client = http_client or HttpTextClient(
            request_delay_seconds=options.request_delay_seconds
        )

    def run(self) -> IndexerResult:
        now = _now()
        state = _RunState(now=now, run_date=now[:10])
        stages: list[StageOutcome] = []
        actions: tuple[tuple[str, Callable[[_RunState], dict[str, int]]], ...] = (
            ("load_library", self._load_library),
            ("crawl", self._crawl),
            ("promote", self._promote),
            ("market", self._market),
            ("verdicts", self._merge_verdicts),
            ("queue", self._queue),
            ("write", self._write),
        )

        failed = False
        for name, action in actions:
            if failed:
                stages.append(StageOutcome(name=name, status="skipped"))
                continue
            try:
                stages.append(StageOutcome(name=name, status="ok", counts=action(state)))
            except Exception as error:
                # Only the exception type reaches the log: a store failure carries the
                # library location, and the log may be public.
                logger.error("indexer stage %s failed: %s", name, type(error).__name__)
                stages.append(
                    StageOutcome(name=name, status="failed", error=type(error).__name__)
                )
                failed = True

        meta = _build_meta(stages, state, self.options, failed)
        if not self.options.dry_run:
            try:
                self.store.write_meta(meta)
            except Exception as error:
                logger.error("indexer could not write meta: %s", type(error).__name__)
                failed = True
                meta["status"] = "failed"
        return IndexerResult(ok=not failed, stages=tuple(stages), meta=meta)

    def _load_library(self, state: _RunState) -> dict[str, int]:
        state.library = self.store.load_listings()
        return {"listings": len(state.library)}

    def _crawl(self, state: _RunState) -> dict[str, int]:
        crawler = SSCrawler(
            start_url=self.options.start_url,
            http_client=self.http_client,
            list_fetch_workers=LIST_FETCH_WORKERS,
            detail_fetch_workers=DETAIL_FETCH_WORKERS,
        )
        state.crawl = crawler.crawl(limit=self.options.limit)
        return {
            "pages": state.crawl.page_count,
            "seen": state.crawl.seen_count,
            "parsed": len(state.crawl.payloads),
            "failed_pages": state.crawl.failed_page_count,
            "failed_details": state.crawl.failed_detail_count,
            "complete": int(state.crawl.complete),
        }

    def _promote(self, state: _RunState) -> dict[str, int]:
        crawl = state.crawl
        if crawl is None:
            raise RuntimeError("promote stage reached without a crawl result")

        state.records = dict(state.library)
        counts = {"new": 0, "changed": 0, "unchanged": 0, "reactivated": 0, "removed": 0}

        for payload in crawl.payloads:
            ss_id = payload.ss_id
            previous = state.library.get(ss_id)
            record = build_record(payload, seen_at=state.now, previous=previous)

            if previous is None:
                state.records[ss_id] = record
                state.events.append(_event(state.now, ss_id, "listing_added"))
                state.new_ids.add(ss_id)
                state.touched_ids.add(ss_id)
                counts["new"] += 1
                continue

            changed = {
                key: value for key, value in diff_records(previous, record).items()
                if key != "status"
            }
            reactivated = previous.get("status", ACTIVE) != ACTIVE
            if not changed and not reactivated:
                counts["unchanged"] += 1
                continue

            state.records[ss_id] = record
            state.touched_ids.add(ss_id)
            if reactivated:
                state.events.append(_event(state.now, ss_id, "listing_reactivated"))
                counts["reactivated"] += 1
            if changed:
                state.changes[ss_id] = changed
                state.events.append(_event(state.now, ss_id, "listing_changed", changed))
                counts["changed"] += 1

        counts["removed"] = self._remove_missing(state, crawl)
        return counts

    def _remove_missing(self, state: _RunState, crawl: CrawlResult) -> int:
        if not crawl.complete:
            # An incomplete crawl knows nothing about what is missing: a failed list page
            # would deactivate every live listing it held.
            state.deactivation_skipped = True
            return 0

        removed = 0
        for ss_id, previous in state.library.items():
            if ss_id in crawl.seen_ss_ids or previous.get("status", ACTIVE) != ACTIVE:
                continue
            state.records[ss_id] = {
                **previous,
                "status": REMOVED,
                "revision": int(previous.get("revision", 0)) + 1,
            }
            state.events.append(_event(state.now, ss_id, "listing_removed"))
            removed += 1
        return removed

    def _market(self, state: _RunState) -> dict[str, int]:
        enriched = apply_market_stats(state.records[ss_id] for ss_id in sorted(state.records))
        state.records = {record["ss_id"]: record for record in enriched}
        priced = sum(1 for record in enriched if record["market"]["price_per_m2"] is not None)
        districts = {
            record["core"].get("district")
            for record in enriched
            if record["market"]["district_sample_size"]
        }
        return {"priced": priced, "districts": len(districts)}

    def _merge_verdicts(self, state: _RunState) -> dict[str, int]:
        days = [day for day in self.store.verdict_days() if day <= state.run_date]
        merged = 0
        unknown = 0
        for day in days:
            for verdict in self.store.read_verdicts(day):
                ss_id = str(verdict.get("ss_id") or "")
                record = state.records.get(ss_id)
                if record is None:
                    unknown += 1
                    continue
                state.records[ss_id] = merge_judgment(record, verdict)
                merged += 1
        return {"days": len(days), "merged": merged, "unknown": unknown}

    def _queue(self, state: _RunState) -> dict[str, int]:
        # Decided after verdicts are merged, so a judged listing whose price moved
        # is queued as "changed" rather than dropped as already judged.
        for ss_id in state.touched_ids:
            record = state.records[ss_id]
            if record.get("status", ACTIVE) != ACTIVE:
                continue
            if ss_id in state.new_ids:
                state.queue_reasons[ss_id] = REASON_NEW
            elif not record.get("judgment"):
                state.queue_reasons[ss_id] = REASON_NEVER_JUDGED
            elif classify_change(state.changes.get(ss_id, {})) == SUBSTANTIVE:
                state.queue_reasons[ss_id] = REASON_CHANGED
        counts = {reason: 0 for reason in (REASON_NEW, REASON_CHANGED, REASON_NEVER_JUDGED)}
        for reason in state.queue_reasons.values():
            counts[reason] += 1
        return counts

    def _write(self, state: _RunState) -> dict[str, int]:
        entries = {
            ss_id: {
                "ss_id": ss_id,
                "reason": state.queue_reasons[ss_id],
                "changed": state.changes.get(ss_id, {}),
                "record": state.records[ss_id],
            }
            for ss_id in state.queue_reasons
        }
        # A rerun on the same day keeps what the earlier run queued: an untouched,
        # still-unjudged listing must not vanish from the queue just because the
        # crawl saw nothing new about it.
        carried = 0
        for earlier in self.store.read_queue(state.run_date):
            ss_id = str(earlier.get("ss_id") or "")
            record = state.records.get(ss_id)
            if ss_id in entries or record is None:
                continue
            if record.get("status", ACTIVE) != ACTIVE or record.get("judgment"):
                continue
            entries[ss_id] = {**earlier, "record": record}
            carried += 1
        counts = {
            "listings": len(state.records),
            "events": len(state.events),
            "queued": len(entries),
            "carried": carried,
        }
        if self.options.dry_run:
            return counts

        self.store.save_listings(state.records)
        self.store.append_events(state.events)
        if entries:
            self.store.write_queue(state.run_date, [entries[ss_id] for ss_id in sorted(entries)])
        return counts


def _build_meta(
    stages: Sequence[StageOutcome],
    state: _RunState,
    options: IndexerOptions,
    failed: bool,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for stage in stages:
        for key, value in stage.counts.items():
            counts[f"{stage.name}.{key}"] = value
    return {
        "finished_at": _now(),
        "run_date": state.run_date,
        "status": "failed" if failed else "ok",
        "dry_run": options.dry_run,
        "crawl_complete": state.crawl.complete if state.crawl else False,
        "deactivation_skipped": state.deactivation_skipped,
        "stages": [
            {
                "name": stage.name,
                "status": stage.status,
                "counts": stage.counts,
                **({"error": stage.error} if stage.error else {}),
            }
            for stage in stages
        ],
        "counts": counts,
    }


def _event(
    now: str,
    ss_id: str,
    event: str,
    changed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"at": now, "ss_id": ss_id, "event": event}
    if changed:
        record["changed"] = dict(changed)
    return record


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
