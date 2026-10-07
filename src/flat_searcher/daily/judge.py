"""Judge the shortlist's candidates that have no verdict yet, giving the GPU back whenever another program needs it."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from flat_searcher.daily.progress import DONE, FAILED, SKIPPED
from flat_searcher.daily.run import StepContext, StepResult
from flat_searcher.judging import JudgeOptions, JudgeRun
from flat_searcher.judging.runner import BinaryFetcher
from flat_searcher.library import LibraryStore
from flat_searcher.shortlist.criteria import Criteria
from flat_searcher.shortlist.digest import select

POLL_SECONDS = 15.0
QUIET_SECONDS = 120.0
START_TIMEOUT_SECONDS = 120.0


class ModelClient(Protocol):
    @property
    def name(self) -> str: ...

    def ask(
        self, system: str, text: str, images_b64: Sequence[str], schema: Mapping[str, Any]
    ) -> str: ...

    def available(self) -> bool: ...

    def unload(self) -> None: ...


class Guard(Protocol):
    def busy(self) -> str | None: ...


def unjudged_candidates(store: LibraryStore, criteria: Criteria) -> tuple[dict[str, list[str]], int]:
    """Candidates without any verdict, by the newest queue day holding each, and how many no queue holds."""
    selection = select(
        store.load_listings(), criteria, store.read_transit(), store.read_events(), None
    )
    missing = {item.ss_id for item in selection.candidates if not item.record.get("judgment")}
    for day in store.verdict_days():
        missing -= {str(verdict.get("ss_id")) for verdict in store.read_verdicts(day)}
    by_day: dict[str, list[str]] = {}
    for day in sorted(store.queue_days(), reverse=True):
        for entry in store.read_queue(day):
            ss_id = str(entry.get("ss_id"))
            if ss_id in missing:
                by_day.setdefault(day, []).append(ss_id)
                missing.discard(ss_id)
    return by_day, len(missing)


class CandidateJudge:
    def __init__(
        self,
        store: LibraryStore,
        client: ModelClient,
        guard: Guard,
        instructions: Path,
        fetcher: BinaryFetcher,
        start_model_server: Callable[[], bool],
        *,
        poll_seconds: float = POLL_SECONDS,
        quiet_seconds: float = QUIET_SECONDS,
        start_timeout_seconds: float = START_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._client = client
        self._guard = guard
        self._instructions = instructions
        self._fetcher = fetcher
        self._start_model_server = start_model_server
        self._poll = poll_seconds
        self._quiet = quiet_seconds
        self._start_timeout = start_timeout_seconds
        self._clock = clock

    def run(self, context: StepContext, plan: Mapping[str, list[str]], unqueued: int) -> StepResult:
        total = sum(len(ids) for ids in plan.values())
        left_out = f"; {unqueued} in no queue" if unqueued else ""
        if total == 0:
            return StepResult(DONE, f"every candidate has a verdict{left_out}", {"judged": 0})
        if not self._ensure_server(context):
            return StepResult(SKIPPED, "Ollama is not reachable", {"judged": 0})

        done = judged = failed = 0
        abort_reason: str | None = None

        def after_each(index: int, count: int, outcome: str) -> None:
            nonlocal done
            done += 1
            context.count(done, total, "listings")
            self._hold(context)

        context.count(0, total, "listings")
        self._hold(context)
        try:
            for day in sorted(plan):
                options = JudgeOptions(
                    day=day,
                    instructions_path=self._instructions,
                    model_name=self._client.name,
                    only_ids=frozenset(plan[day]),
                )
                result = JudgeRun(self._store, options, self._client.ask, self._fetcher).run(
                    progress=after_each
                )
                judged += result.judged
                failed += result.failed
                if result.aborted:
                    abort_reason = result.abort_reason
                    break
        finally:
            self._client.unload()
        summary = f"judged={judged} failed={failed}{left_out}"
        if abort_reason:
            summary += f"; {abort_reason}"
        status = FAILED if abort_reason and judged == 0 else DONE
        return StepResult(status, summary, {"judged": judged})

    def _ensure_server(self, context: StepContext) -> bool:
        if self._client.available():
            return True
        context.note("starting Ollama")
        if not self._start_model_server():
            return False
        deadline = self._clock() + self._start_timeout
        while self._clock() < deadline:
            context.sleep(2.0)
            if self._client.available():
                return True
        return False

    def _hold(self, context: StepContext) -> None:
        """Returns once the GPU has been free for the quiet period; the model is unloaded meanwhile."""
        reason = self._guard.busy()
        if reason is None:
            return
        self._client.unload()
        context.wait(f"Waiting for the GPU: {reason}")
        free_since: float | None = None
        while True:
            context.sleep(self._poll)
            now = self._guard.busy()
            if now is not None:
                free_since = None
                if now != reason:
                    reason = now
                    context.wait(f"Waiting for the GPU: {reason}")
                continue
            if free_since is None:
                free_since = self._clock()
            if self._clock() - free_since >= self._quiet:
                break
        context.resume()
        context.log("GPU free: judging resumes")
