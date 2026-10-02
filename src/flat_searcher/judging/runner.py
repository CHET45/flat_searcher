"""Judge a day's queue with a local model and append verdicts one at a time."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from flat_searcher.judging.errors import PromptTooLargeError
from flat_searcher.judging.hints import hints_for
from flat_searcher.judging.schema import VERDICT_SCHEMA, VerdictError, finalize_verdict, price_assessment
from flat_searcher.library.store import LibraryStore
from flat_searcher.scraper.http_client import FetchError

MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_MAX_IMAGES = 8
# SS.com galleries put the floor plan last; keep the head for the main photos.
HEAD_IMAGES = 3
# Agency descriptions run to 20k characters; the model needs the gist, and the
# deterministic hints are extracted from the full text before trimming.
MAX_DESCRIPTION_CHARS = 3000
MAX_FIELD_CHARS = 160
MIN_IMAGES_ON_RETRY = 2

logger = logging.getLogger(__name__)

AskFunction = Callable[[str, str, Sequence[str], Mapping[str, Any]], str]


class BinaryFetcher(Protocol):
    def fetch_bytes(self, url: str) -> Any: ...


@dataclass(frozen=True)
class JudgeFilters:
    max_price: int | None = None
    min_rooms: int | None = None
    districts: frozenset[str] = frozenset()

    def accepts(self, record: Mapping[str, Any]) -> bool:
        core: Mapping[str, Any] = record.get("core") or {}
        price = core.get("price_eur")
        rooms = core.get("declared_rooms")
        if self.max_price is not None and (price is None or price > self.max_price):
            return False
        if self.min_rooms is not None and (rooms is None or rooms < self.min_rooms):
            return False
        if self.districts and str(core.get("district") or "").casefold() not in self.districts:
            return False
        return True


@dataclass(frozen=True)
class JudgeOptions:
    day: str
    instructions_path: Path
    model_name: str
    limit: int | None = None
    max_images: int = DEFAULT_MAX_IMAGES
    filters: JudgeFilters = field(default_factory=JudgeFilters)
    only_ids: frozenset[str] = frozenset()
    force: bool = False
    shard: tuple[int, int] = (0, 1)
    dry_run: bool = False


@dataclass(frozen=True)
class JudgeResult:
    day: str
    queued: int
    eligible: int
    already_judged: int
    judged: int
    failed: int
    aborted: bool = False
    abort_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.aborted


class JudgeRun:
    def __init__(
        self,
        store: LibraryStore,
        options: JudgeOptions,
        ask: AskFunction,
        fetcher: BinaryFetcher,
    ) -> None:
        self.store = store
        self.options = options
        self.ask = ask
        self.fetcher = fetcher

    def run(self, progress: Callable[[int, int, str], None] | None = None) -> JudgeResult:
        day = self.options.day
        queue = self.store.read_queue(day)
        judged_ids = {str(v.get("ss_id")) for v in self.store.read_verdicts(day)}
        eligible = [
            entry
            for entry in queue
            if self.options.filters.accepts(entry.get("record") or {})
            and (not self.options.only_ids or str(entry.get("ss_id")) in self.options.only_ids)
        ]
        pending = [
            entry
            for entry in eligible
            if self.options.force or str(entry.get("ss_id")) not in judged_ids
        ]
        index, count = self.options.shard
        pending = [entry for position, entry in enumerate(pending) if position % count == index]
        if self.options.limit is not None:
            pending = pending[: self.options.limit]

        instructions = self.options.instructions_path.read_text(encoding="utf-8")
        judged = 0
        failed = 0
        consecutive = 0
        abort_reason: str | None = None
        for index, entry in enumerate(pending, start=1):
            ss_id = str(entry.get("ss_id"))
            try:
                verdict = self._judge(entry, instructions)
                if not self.options.dry_run:
                    self.store.append_verdicts(day, [verdict])
                judged += 1
                consecutive = 0
                outcome = f"score={verdict['score']}"
            except Exception as error:
                failed += 1
                consecutive += 1
                outcome = f"failed: {type(error).__name__}"
                logger.warning("judge failed for %s: %s", ss_id, error)
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    abort_reason = (
                        f"Aborted after {consecutive} consecutive failures; last: "
                        f"{type(error).__name__}"
                    )
            if progress is not None:
                progress(index, len(pending), outcome)
            if abort_reason:
                logger.error("%s", abort_reason)
                break

        return JudgeResult(
            day=day,
            queued=len(queue),
            eligible=len(eligible),
            already_judged=sum(1 for entry in eligible if str(entry.get("ss_id")) in judged_ids),
            judged=judged,
            failed=failed,
            aborted=abort_reason is not None,
            abort_reason=abort_reason,
        )

    def _judge(self, entry: Mapping[str, Any], instructions: str) -> dict[str, Any]:
        positions, images = self._download_images(entry["record"])
        while True:
            try:
                return self._judge_with(entry, instructions, positions, images)
            except PromptTooLargeError:
                if len(images) <= MIN_IMAGES_ON_RETRY:
                    raise
                keep = max(MIN_IMAGES_ON_RETRY, len(images) // 2)
                logger.warning("prompt too large; retrying with %s images", keep)
                positions, images = positions[:keep], images[:keep]

    def _judge_with(
        self,
        entry: Mapping[str, Any],
        instructions: str,
        positions: Sequence[int],
        images: Sequence[str],
    ) -> dict[str, Any]:
        record: Mapping[str, Any] = entry["record"]
        assessment = price_assessment(record)
        hints = hints_for(record)
        trimmed = {**entry, "record": trim_record(record), "changed": trim_changed(entry.get("changed") or {})}
        attached = (
            f"gallery positions {', '.join(str(p) for p in positions)}" if positions else "none"
        )
        text = (
            "Queue entry (one JSON object):\n"
            + json.dumps({**trimmed, "hints": hints} if hints else trimmed, ensure_ascii=False)
            + "\n\nComputed price assessment - copy these four values verbatim and build your "
            "reasoning on them:\n"
            + json.dumps(assessment)
            + f"\n\n{len(images)} of {len(record.get('images') or [])} listing images are attached: "
            + attached
            + ".\n\nRespond with exactly one JSON object as specified. "
            "No markdown fences, no prose outside the object."
        )
        # A small model occasionally loops inside a string until the token limit
        # cuts it mid-sentence; one retry with a brevity reminder recovers most.
        attempts = (text, text + "\n\nKeep layout_notes under 60 words and verdict under 150 words.")
        last_error: VerdictError | None = None
        for prompt in attempts:
            try:
                return self._parse(self.ask(instructions, prompt, images, VERDICT_SCHEMA), entry)
            except VerdictError as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _parse(self, raw_text: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise VerdictError(f"model output is not JSON: {error.msg}") from error
        if not isinstance(raw, dict):
            raise VerdictError("model output is not a JSON object")
        return finalize_verdict(
            raw,
            entry,
            model=self.options.model_name,
            judged_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _download_images(self, record: Mapping[str, Any]) -> tuple[list[int], list[str]]:
        gallery = list(record.get("images") or [])
        positions: list[int] = []
        encoded: list[str] = []
        for index in select_image_positions(len(gallery), self.options.max_images):
            image = gallery[index]
            url = image.get("url") if isinstance(image, dict) else None
            if not url:
                continue
            try:
                content = self.fetcher.fetch_bytes(url).content
            except (FetchError, OSError) as error:
                logger.warning("image skipped: %s", type(error).__name__)
                continue
            positions.append(index + 1)
            encoded.append(base64.b64encode(content).decode("ascii"))
        return positions, encoded


def trim_record(record: Mapping[str, Any]) -> dict[str, Any]:
    text: Mapping[str, Any] = record.get("text") or {}
    description = str(text.get("description") or "")
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip() + " […truncated]"
    fields = {
        key: (value if len(str(value)) <= MAX_FIELD_CHARS else str(value)[:MAX_FIELD_CHARS] + "…")
        for key, value in (record.get("fields") or {}).items()
    }
    return {**record, "text": {**text, "description": description}, "fields": fields}


def trim_changed(changed: Mapping[str, Any]) -> dict[str, Any]:
    # The record already carries the new values; the model only needs to know
    # what moved, and a before/after pair of 20k-character descriptions does not fit.
    return {key: [_trim_value(value) for value in pair] for key, pair in changed.items()}


def _trim_value(value: Any) -> Any:
    if isinstance(value, list):
        return f"{len(value)} images" if value and isinstance(value[0], dict) else f"{len(value)} items"
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "…"
    return value


def select_image_positions(gallery_size: int, max_images: int) -> list[int]:
    if max_images <= 0 or gallery_size <= 0:
        return []
    if gallery_size <= max_images:
        return list(range(gallery_size))
    head = min(HEAD_IMAGES, max_images)
    return list(range(head)) + list(range(gallery_size - (max_images - head), gallery_size))
