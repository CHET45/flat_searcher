"""The day's loop as a list of steps that resumes where an interrupted run of the same day stopped."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from flat_searcher.daily.progress import (
    DONE,
    FAILED,
    FINISHED,
    PENDING,
    STOPPED,
    ProgressFile,
    read_progress,
)

ALREADY_DONE = "already done"

logger = logging.getLogger(__name__)


class StopRequested(BaseException):
    """Raised at a checkpoint once a stop was asked for.

    A BaseException, so the `except Exception` around each indexer stage lets it through
    instead of recording a failed stage.
    """


@dataclass(frozen=True)
class StepResult:
    status: str
    summary: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


class StepContext:
    def __init__(
        self,
        progress: ProgressFile,
        name: str,
        stop_requested: Callable[[], bool],
        results: Mapping[str, Mapping[str, Any]],
        sleep: Callable[[float], None],
    ) -> None:
        self.name = name
        self.results = results
        self._progress = progress
        self._stop_requested = stop_requested
        self._sleep = sleep

    def check(self) -> None:
        if self._stop_requested():
            raise StopRequested

    def count(self, done: int, total: int, unit: str) -> None:
        self.check()
        self._progress.count(self.name, done, total, unit)

    def note(self, text: str) -> None:
        self._progress.note(self.name, text)

    def wait(self, reason: str) -> None:
        self._progress.wait(self.name, reason)

    def resume(self) -> None:
        self._progress.resume(self.name)

    def log(self, line: str) -> None:
        self._progress.log(line)

    def sleep(self, seconds: float) -> None:
        """Sleeps in short slices so a stop request is honoured within a second."""
        left = seconds
        while left > 0:
            self.check()
            self._sleep(min(1.0, left))
            left -= 1.0
        self.check()


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    action: Callable[[StepContext], StepResult]


class DailyRun:
    def __init__(
        self,
        progress: ProgressFile,
        steps: Sequence[Step],
        stop_requested: Callable[[], bool],
        day: str,
        pid: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._progress = progress
        self._steps = steps
        self._stop_requested = stop_requested
        self._day = day
        self._pid = pid
        self._sleep = sleep

    def run(self, fresh: bool = False) -> str:
        earlier = read_progress(self._progress.path)
        if (
            not fresh
            and earlier is not None
            and earlier.get("day") == self._day
            and earlier.get("state") == DONE
        ):
            return ALREADY_DONE
        progress = self._progress
        progress.begin(self._day, [(step.name, step.title) for step in self._steps], self._pid, fresh=fresh)
        results: dict[str, Mapping[str, Any]] = {}
        for step in self._steps:
            record = progress.step(step.name)
            if record["status"] in FINISHED:
                results[step.name] = record.get("data") or {}
                continue
            context = StepContext(progress, step.name, self._stop_requested, results, self._sleep)
            try:
                context.check()
                progress.start_step(step.name)
                result = step.action(context)
            except StopRequested:
                if progress.step(step.name)["status"] not in (*FINISHED, PENDING):
                    progress.finish_step(step.name, STOPPED)
                progress.finish(STOPPED)
                return STOPPED
            except Exception as error:
                # The type only: a library failure carries its location in the message.
                logger.error("daily step %s failed: %s", step.name, type(error).__name__)
                result = StepResult(FAILED, f"error: {type(error).__name__}")
            progress.finish_step(step.name, result.status, result.summary, result.data)
            if result.status == FAILED:
                progress.finish(FAILED)
                return FAILED
            results[step.name] = result.data
        progress.finish(DONE)
        return DONE
